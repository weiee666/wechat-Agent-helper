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

    对方不在线 / 找不到 → 工具返回明确原因；你如实告诉用户。"""
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
                f"可能这个人还没开通 bot，或还没设置自己的名字。")
    if target.user_id == user_id:
        return "[call_agent] 目标就是你自己，不能自己跟自己对话。"
    if target.status != "running":
        return (f"[call_agent] 「{target.display_name or target.user_id}」的 Agent 当前不在线，"
                f"消息发不出去。可以晚点再试。")

    sender = users.get(user_id)
    sender_display = (sender.display_name if sender and sender.display_name else "某个用户")
    target_label = target.display_name or target.user_id
    target_require_auth = UserSettingsStore().get_require_authorization(target.user_id)

    # 双方 channel 都推同一条 agent_conversation 事件，两边看板都能看到这一轮
    def _publish_both(payload: dict) -> None:
        realtime.publish(user_id, "agent_conversation", payload)
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


# 所有工具（runner 直接 bind_tools(TOOLS)）
TOOLS = [
    structure_task, start_recording,
    set_my_name, set_agent_name, call_agent,
    web_search,
]
BY_NAME = {t.name: t for t in TOOLS}
