# -*- coding: utf-8 -*-
"""Agent 工具：LangChain 原生 @tool。

user_id（= from_user_id，用户微信号）用 InjectedToolArg 注入——对模型不可见，由 runner 执行时填入。
"""
from __future__ import annotations

from typing import Annotated

from langchain_core.tools import InjectedToolArg, tool

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
    """向另一个用户的 Agent 发一条消息，由对方 Agent 处理后回复你。target_name 是对方的
    姓名或对方 Agent 的名字（会在全局 bot 用户表 bot_users 里查找，不是查通讯录）。

    **触发场景**：用户说「问危博xxx」/「让危博的助手帮我确认xxx」/「跟危博的Agent说xxx」/
    「联系一下危博」/「问下小博xxx」/「你现在去找下危博的助手」这种**要跟另一个用户的
    助手交流**的意图 → 一定用这个工具，不要用别的工具（本项目没有查通讯录的功能）。

    重要：
    - 对方可能不在线，或没设名字找不到，工具会返回明确原因
    - 两个 Agent 间对话有 5 轮上限，防止无限对话
    - 对方 Agent 的回复以文本形式返回给你，你要综合它的回复给用户最终答案"""
    from app import realtime
    from app.agent import conversation
    from app.agent.runner import VoiceTaskAgent

    target_name = (target_name or "").strip()
    message = (message or "").strip()
    if not target_name or not message:
        return "[call_agent] target_name 和 message 都不能空"

    # 5 轮硬限制（每 call_agent 一次算一轮）
    round_num = conversation.inc_round()
    if round_num > conversation.MAX_ROUNDS:
        return (f"[call_agent] 已经和其他 Agent 交换了 {conversation.MAX_ROUNDS} 轮消息，"
                f"到达上限，本次调用被阻止。请直接回复用户当前进展。")

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

    # 双方 channel 都推同一条 agent_conversation 事件，两边看板都能看到这一轮
    def _publish_both(payload: dict) -> None:
        realtime.publish(user_id, "agent_conversation", payload)
        realtime.publish(target.user_id, "agent_conversation", payload)

    _publish_both({
        "round": round_num,
        "direction": "→",
        "from": sender_display,
        "from_user_id": user_id,
        "to": target.display_name or target.user_id,
        "to_user_id": target.user_id,
        "text": message,
    })

    # 同步触发目标 Agent 处理
    try:
        agent = VoiceTaskAgent()
        reply = agent.handle_agent_message(
            target_user_id=target.user_id,
            from_user_id=user_id,
            from_display_name=sender_display,
            message=message,
        )
    except Exception as e:  # noqa: BLE001
        return f"[call_agent] 目标 Agent 处理失败：{e}"

    _publish_both({
        "round": round_num,
        "direction": "←",
        "from": target.display_name or target.user_id,
        "from_user_id": target.user_id,
        "to": sender_display,
        "to_user_id": user_id,
        "text": reply,
    })

    return f"「{target.display_name or target.user_id}」的 Agent 回复：\n{reply}"


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


# 所有工具（runner 直接 bind_tools(TOOLS)）
TOOLS = [
    structure_task, start_recording,
    set_my_name, set_agent_name, call_agent,
]
BY_NAME = {t.name: t for t in TOOLS}
