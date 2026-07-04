# -*- coding: utf-8 -*-
"""Agent 工具：LangChain 原生 @tool。

user_id（= from_user_id，用户微信号）用 InjectedToolArg 注入——对模型不可见，由 runner 执行时填入。
"""
from __future__ import annotations

from typing import Annotated

from langchain_core.tools import InjectedToolArg, tool

from app import config
from app.agent import summarizer
from app.core.memory.bot_users import BotUsersStore
from app.core.memory.recording import RecordingStore

# 注入参数类型别名（对模型不可见）。user_id = from_user_id（用户微信号），稳定的用户主键。
UserId = Annotated[str, InjectedToolArg]


# ── structure_task ──────────────────────────────────────────
@tool
def structure_task(content: str, user_id: UserId = "default") -> str:
    """把用户口述的内容整理成简明的任务记录。当用户**在同一句话里就把要记的内容说全了**
    （如「记一下：明天三点前交报销单」）时调用，立即整理成简明记录。"""
    content = (content or "").strip()
    if not content:
        return "[structure_task] 没有可记录的内容"
    return f"📝 已记录：\n{summarizer.summarize(content)}"


# ── start_recording ─────────────────────────────────────────
@tool
def start_recording(user_id: UserId = "default") -> str:
    """开始一段「多条录制」会话：之后用户会连续发多条语音/文字，全部说完后发『就这些』生成
    整段总结。当用户说「我想记录一下 / 开始记录 / 我要记一段」这类**还没给具体内容、准备连发
    多条**的指令时调用。如果用户当场就把内容说全了，用 structure_task 而不是这个。"""
    RecordingStore().start(user_id)
    return ("🎙️ 开始记录，你尽管说，我先攒着不打断；说完发一句『就这些』我来整理。")


# ── call_agent ──────────────────────────────────────────────
@tool
def call_agent(target_name: str, message: str, user_id: UserId = "default") -> str:
    """向另一个用户的 Agent 发一条消息，进行跨 Agent 对话。target_name 是对方用户姓名或对方
    Agent 名字（在全局 bot 用户表里查找，不是通讯录）。

    **触发场景**：用户说「问XX」/「联系XX」/「跟XX说xxx」/「让XX的助手确认xxx」。

    **工作方式（默认直连模式）**：
    - 对方 Agent 会同步生成回复，作为本工具返回值给你
    - 消息**不会**打扰对方用户微信（Agent 间对话只在旁观面板显示）
    - 你要**综合对方的回复**给自己的用户一个自然的回应
    - 对方的回复如果没把事情说清、需要澄清才能完成用户交办的事，你**可以再次调用 call_agent
      追问**（5 轮上限，每次 call_agent 算一轮）
    - 简单社交寒暄（打招呼）1 轮就够；协商类（约时间、确认方案）通常要 2-3 轮

    **如果对方开启了"授权模式"**（少见）：
    - 对方回复需要 ta 本人在微信里"允许"，异步流程
    - 工具会返回"等待授权"，你告诉用户"已发起，等应答"就好

    对方不在线 / 找不到 → 工具返回明确原因；**如实告诉用户"没找到 XX"**，
    **绝对不要**自作主张换成 bot_users 表里的别人。

    可以调用的 target 包括：
    - 具体的人（如"危博"、"危呃呃"）：对方是普通用户助手
    - **"老师"**：跨用户共享的教学 Agent，专门帮用户讲清楚复杂概念（苏格拉底+费曼式教学）
      用户说「找老师讲讲XX / 问老师 / 让老师教我」→ call_agent(target_name="老师", ...)"""
    from app import realtime
    from app.agent import conversation
    from app.agent.runner import VoiceTaskAgent
    from app.core.memory.settings import UserSettingsStore
    from app.kernel import bus

    target_name = (target_name or "").strip()
    message = (message or "").strip()
    if not target_name or not message:
        return "[call_agent] target_name 和 message 都不能空"

    # 5 轮硬限制（每 call_agent 一次算一轮）
    round_num = conversation.inc_round()
    if round_num > conversation.MAX_ROUNDS:
        return (f"[call_agent] 已经和其他 Agent 交换了 {conversation.MAX_ROUNDS} 轮消息，"
                f"到达上限。请直接回复用户当前进展。")

    users = BotUsersStore()
    target = users.find_by_name(target_name)
    if target is None:
        return (f"[call_agent] 全局 bot 用户表里没找到叫「{target_name}」的用户。"
                f"可能这个人还没开通 bot，或还没设置自己的名字。"
                f"**请如实告诉用户「没找到」，绝对不要换成表里的别人。**")
    if target.user_id == user_id:
        return "[call_agent] 目标就是你自己，不能自己跟自己对话。"
    if target.status != "running":
        return (f"[call_agent] 「{target.display_name or target.user_id}」的 Agent 当前不在线，"
                f"消息发不出去。可以晚点再试。")

    sender = users.get(user_id)
    sender_display = (sender.display_name if sender and sender.display_name else "某个用户")
    target_label = target.display_name or target.user_id
    is_system_target = target.user_id.startswith("system:")
    target_require_auth = (not is_system_target) and UserSettingsStore().get_require_authorization(target.user_id)

    # 双方 channel 都推同一条 agent_conversation 事件，两边看板都能看到这一轮
    # 系统 Agent（如老师）没有 pusher channel，只推 sender 那一侧就好
    def _publish_both(payload: dict) -> None:
        realtime.publish(user_id, "agent_conversation", payload)
        if not is_system_target:
            realtime.publish(target.user_id, "agent_conversation", payload)

    _publish_both({
        "round": round_num,
        "direction": "→",
        "from": sender_display,
        "from_user_id": user_id,
        "to": target_label,
        "to_user_id": target.user_id,
        "text": message,
    })

    def _persist_pair(sent_text: str, reply_text: str) -> None:
        """落到 pair 共享 session_id，让"我的助手 ↔ 对方" tab 能拉到独立历史。"""
        try:
            from app.core.memory.short_term import ShortTermMemory
            from app.models.enums import MessageRole
            from app.models.schemas import Message
            x, y = sorted([user_id, target.user_id])
            pair_sid = f"pair:{x}|{y}"
            stm = ShortTermMemory()
            stm.add_message(pair_sid, Message(
                role=MessageRole.USER, content=sent_text,
                metadata={"from_user_id": user_id, "from_display_name": sender_display,
                          "round": round_num},
            ))
            stm.add_message(pair_sid, Message(
                role=MessageRole.USER, content=reply_text,
                metadata={"from_user_id": target.user_id, "from_display_name": target_label,
                          "round": round_num},
            ))
        except Exception as e:  # noqa: BLE001
            import logging as _log
            _log.getLogger(__name__).warning("pair 落库失败: %s", e)

    # 特殊路径：如果 target 是系统 Agent（如老师），走对应的 handler
    if target.user_id == "system:teacher":
        from app.agent import teacher as _teacher
        try:
            # 助手代问老师 → 不 publish 到用户的老师 tab（避免污染"跟老师聊"tab）
            # persist=False：不落 teacher:{uid}，改落"助手↔老师"独立 pair 库
            reply = _teacher.handle(user_id, message,
                                    publish_channel_events=False, persist=False)
        except Exception as e:  # noqa: BLE001
            return f"[call_agent] 老师处理失败：{e}"
        reply = (reply or "").strip() or "（老师暂无回复）"
        _publish_both({
            "round": round_num,
            "direction": "←",
            "from": target_label,
            "from_user_id": target.user_id,
            "to": sender_display,
            "to_user_id": user_id,
            "text": reply,
        })
        _persist_pair(message, reply)
        return (f"「老师」回复：\n{reply}\n\n"
                f"[提示] 老师用苏格拉底+费曼方法讲解。老师的回复中：\n"
                f"- 如果是**解释**：请综合成一段自然的中文给你的用户\n"
                f"- 如果是**反问**（比如 '你觉得 XX 是什么？'）：**原样传给用户**，不要代答")

    # 特殊路径：Claude Agent（通过 WS 连到用户 Mac 上的 claude CLI）
    if target.user_id == "system:claude":
        from app.agent import claude_agent as _claude
        if not _claude.hub.is_online():
            return ("[call_agent] Claude 目前不在线：需要用户 Mac 上启动 claude_bridge daemon 连接过来。")
        try:
            reply = _claude.handle(user_id, message, persist=False)
        except Exception as e:  # noqa: BLE001
            return f"[call_agent] Claude 处理失败：{e}"
        reply = (reply or "").strip() or "（Claude 暂无回复）"
        _publish_both({
            "round": round_num,
            "direction": "←",
            "from": target_label,
            "from_user_id": target.user_id,
            "to": sender_display,
            "to_user_id": user_id,
            "text": reply,
        })
        _persist_pair(message, reply)
        return (f"「Claude」回复：\n{reply}\n\n"
                f"[提示] Claude 是编程专家（跑在用户 Mac 上的 claude CLI）。请把它的回复"
                f"综合成一段中文告诉用户；技术细节（命令、代码片段）保留原样。")

    # 授权模式开启 → 走异步 push + 授权流（用户 A 会等 B 用户批准）
    if target_require_auth:
        handshake_ok = bus.deliver_handshake(
            from_user_id=user_id, from_display=sender_display,
            to_user_id=target.user_id, message=message,
        )
        if not handshake_ok:
            return (f"[call_agent] 「{target_label}」开启了授权模式，但无法送达握手到 ta 微信"
                    f"（ctx 无或已过期）。目前联系不上。")
        try:
            VoiceTaskAgent().handle_agent_message(
                target_user_id=target.user_id, from_user_id=user_id,
                from_display_name=sender_display, message=message,
                request_authorization=True,
            )
        except Exception as e:  # noqa: BLE001
            return f"[call_agent] 目标 Agent 处理失败：{e}"
        bus.deliver_system_notice(
            user_id,
            f"✅ 已向「{target_label}」发起对话。ta 开启了授权模式，"
            f"回复要等 ta 在微信里批准后才会到你这里。"
        )
        conversation.mark_reply_pushed()  # A Agent 沉默
        return (f"[call_agent] 已发起，等对方用户授权。授权模式下你无需综合，"
                f"回复会稍后由系统推到你用户微信里。请直接结束这一轮。")

    # 默认直连模式：同步拿 reply，不打扰任何用户微信
    try:
        reply = VoiceTaskAgent().handle_agent_message(
            target_user_id=target.user_id, from_user_id=user_id,
            from_display_name=sender_display, message=message,
            request_authorization=False,
        )
    except Exception as e:  # noqa: BLE001
        return f"[call_agent] 目标 Agent 处理失败：{e}"

    reply = (reply or "").strip() or "（对方未回复）"
    _publish_both({
        "round": round_num,
        "direction": "←",
        "from": target_label,
        "from_user_id": target.user_id,
        "to": sender_display,
        "to_user_id": user_id,
        "text": reply,
    })

    # ── 落 pair 对话 SQLite（双方看板 pair tab 都能拉到）──
    _persist_pair(message, reply)

    return (f"「{target_label}」的助手回复：\n{reply}\n\n"
            f"[提示] 请综合对方回复给你的用户一个自然的中文回应。用户微信不会自动看到原文。"
            f"如果对方回复没把事情说清、需要追问才能完成用户交办的事，可以再调用 call_agent 追问"
            f"（第 {round_num}/{conversation.MAX_ROUNDS} 轮，还剩 {conversation.MAX_ROUNDS - round_num} 次）。")


# ── set_my_name ─────────────────────────────────────────────
@tool
def set_my_name(name: str, user_id: UserId = "default") -> str:
    """设置或修改当前用户自己的姓名（用于让别人的 Agent 通过名字找到你）。
    当用户说「我叫xxx」/「我是xxx」/「记一下我叫xxx」/「设置我的名字为xxx」时调用。"""
    name = (name or "").strip()
    if not name:
        return "[set_my_name] 没说姓名"
    BotUsersStore().set_display_name(user_id, name)
    return f"👤 记住了，你叫「{name}」。别人的 Agent 说'找{name}'时能找到你。"


# ── set_agent_name ──────────────────────────────────────────
@tool
def set_agent_name(name: str, user_id: UserId = "default") -> str:
    """给用户的 Agent（也就是"我"）起个名字。当用户说「给你起名叫xxx」/
    「你就叫xxx吧」/「以后叫你xxx」时调用。"""
    name = (name or "").strip()
    if not name:
        return "[set_agent_name] 没说名字"
    BotUsersStore().set_agent_name(user_id, name)
    return f"🤖 好的，我叫「{name}」。之后我跟别人的 Agent 沟通会自称这个名字。"


# ── web_search ──────────────────────────────────────────────
@tool
def web_search(query: str, user_id: UserId = "default") -> str:
    """通过 Tavily 联网搜索获取**最新事实**。仅在以下场景才用，其他一律不调：
    - 用户问的是**时效性事实**：当前价格、汇率、新闻、比赛结果、天气、股市、刚发生的事件
    - 用户明确说"查一下 / 搜一下 / 帮我查"
    - 你自己知识库里没有的**具体信息**（比如某个新产品的规格）

    **绝对不用**的场景：
    - 日常聊天、寒暄、情感交流
    - 用户让你**记录 / 记一下**（用 structure_task / start_recording）
    - 用户让你**联系某人**（用 call_agent）
    - 通用建议 / 创意 / 头脑风暴（你自己就能答）
    - 数学计算 / 已知常识（哪年出生、地理常识等）

    query 用简短的中文关键词，别加"请问"、"帮我查一下"等前缀。"""
    query = (query or "").strip()
    if not query:
        return "[web_search] 缺少查询词"
    if not config.TAVILY_API_KEY:
        return "[web_search] 服务端未配置 TAVILY_API_KEY，无法联网搜索。"
    try:
        from langchain_tavily import TavilySearch
        searcher = TavilySearch(
            tavily_api_key=config.TAVILY_API_KEY,
            max_results=5,
            search_depth="basic",
            include_answer="basic",
        )
        result = searcher.invoke({"query": query})
    except Exception as e:  # noqa: BLE001
        return f"[web_search] 搜索失败：{e}"

    # 格式化（简洁，避免噪音）
    answer = (result.get("answer") or "").strip()
    items = result.get("results") or []
    lines = [f"🔍 搜索：{query}"]
    if answer:
        lines.append(f"\n**摘要**：{answer}")
    if items:
        lines.append("\n**结果**：")
        for i, r in enumerate(items[:5], 1):
            title = (r.get("title") or "").strip()
            content = (r.get("content") or "").strip().replace("\n", " ")[:220]
            url = (r.get("url") or "").strip()
            lines.append(f"{i}. **{title}**\n   {content}\n   {url}")
    if not answer and not items:
        return f"🔍 搜索「{query}」没找到结果。"
    return "\n".join(lines)


# ── notify_my_user ─────────────────────────────────────────
@tool
def notify_my_user(text: str, user_id: UserId = "default") -> str:
    """把一段消息主动推到当前 Agent 所服务的**本用户**的微信里。

    **只在跨 Agent 对话场景中使用**：当另一个用户的 Agent 让你转达一句话给你的用户
    （打招呼、通知、告知、祝福这类"应该让本人看到"的内容）时，除了给对方 Agent 回一句
    简短的确认，你**必须**再调这个工具，把消息真正传达到本用户的微信里 —— 否则用户
    永远不会知道有人托你带话。

    text 里要注明**是谁委托的**，例如：
    - "危博让我告诉你：晚安！"
    - "小明说他明天下午两点在楼下等你"
    - "小张祝你生日快乐～"

    **不要**调用这个工具的场景：
    - 对方 Agent 是纯问询（"帮我问你用户几点了" / "他明天有空吗"）—— 你自己知道就答对方，
      不知道就答"这个我不清楚"，不需要打扰用户
    - 只是社交寒暄不需要用户拍板（对方 Agent 打招呼、道谢等）
    - 用户不在跨 Agent 场景中（跟你直接对话时用不上）
    """
    from app import realtime as _realtime
    from app.channel import dispatch
    from app.agent import memory_bridge
    from app.models.enums import MessageRole as _MR

    text = (text or "").strip()
    if not text:
        return "[notify_my_user] text 不能空"
    ok = dispatch.push_to_user(user_id, text)
    # 无论 iLink 是否送达，都同步到看板 self tab（用户在看板里也应该能看到
    # Agent 主动说了什么，避免 microwechat / 看板视图不一致）
    try:
        _realtime.publish(user_id, "assistant_reply", {"text": text})
    except Exception:  # noqa: BLE001
        pass
    try:
        memory_bridge.save_turn(user_id, _MR.ASSISTANT, text)
    except Exception:  # noqa: BLE001
        pass
    if ok:
        return f"✅ 已把 「{text[:40]}...」 推送到你用户的微信 + 看板 self tab"
    return ("⚠️ 微信推送失败（用户可能从没跟 bot 说过话缺 ctx，或 bot 离线）。"
            "看板 self tab 已同步。请在对话中告诉委托的对方 Agent「用户暂时收不到，请ta稍后自己联系」")


# 所有工具（runner 直接 bind_tools(TOOLS)）
TOOLS = [
    structure_task, start_recording,
    set_my_name, set_agent_name, call_agent,
    web_search, notify_my_user,
]
BY_NAME = {t.name: t for t in TOOLS}
