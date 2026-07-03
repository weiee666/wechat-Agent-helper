# -*- coding: utf-8 -*-
"""老师 Agent：跨用户共享的教学助手，每用户对话独立并持久化到 SQLite（stm_messages）。

- 每用户独立 session，session_id = f"teacher:{user_id}"
- 走 ShortTermMemory（跟"我和助手"同一套代码），自动窗口滑动 + LLM 压缩
- 服务重启不丢历史
- 工具：web_search + structure_task
- realtime 事件：teacher_* 系列
"""
from __future__ import annotations

import logging

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from app import config, realtime
from app.agent import tools as agent_tools
from app.agent.llm import get_llm
from app.core.memory.short_term import ShortTermMemory
from app.models.enums import MessageRole
from app.models.schemas import Message

logger = logging.getLogger(__name__)

_MAX_TOOL_ITERS = 5
_TEACHER_TOOL_NAMES = ("web_search", "structure_task")


def _session_id(user_id: str) -> str:
    return f"teacher:{user_id}"


def _load_teacher_prompt() -> str:
    return (config.PROMPT_DIR / "teacher_system.txt").read_text(encoding="utf-8")


def _teacher_tools() -> list:
    return [t for t in agent_tools.TOOLS if t.name in _TEACHER_TOOL_NAMES]


def _teacher_tools_by_name() -> dict:
    return {t.name: t for t in _teacher_tools()}


def _stm() -> ShortTermMemory:
    """短期记忆（带 LLM 压缩）。"""
    return ShortTermMemory(
        llm=get_llm(),
        window_size=config.STM_WINDOW_SIZE,
        max_tokens=config.STM_MAX_TOKENS,
    )


def _stm_messages_to_langchain(msgs: list[Message]) -> list:
    """把 stm 里存的 Message 列表转成 LangChain BaseMessage 列表。"""
    out = []
    for m in msgs:
        if m.role == MessageRole.USER:
            out.append(HumanMessage(content=m.content))
        elif m.role == MessageRole.ASSISTANT:
            out.append(AIMessage(content=m.content))
        else:
            out.append(SystemMessage(content=m.content))
    return out


class TeacherAgent:
    """老师 Agent 单例。每次 handle_message 从 stm 读历史 → LLM → 存 turn 回 stm。"""

    def handle_message(self, user_id: str, text: str,
                       publish_channel_events: bool = True) -> str:
        """publish_channel_events:
          True（默认）：向 user 的 pusher channel 推 teacher_* 事件（看板 tab 直接对话时）
          False：不推（助手代问时，避免污染看板 tab）"""
        text = (text or "").strip()
        if not text:
            return "（我在。你想问什么？）"

        if publish_channel_events:
            realtime.publish(user_id, "teacher_user_message", {"text": text})

        sid = _session_id(user_id)
        stm = _stm()

        # 从 stm 拉历史，转成 LangChain messages
        history = stm.get_history(sid)
        lc_history = _stm_messages_to_langchain(history)

        messages = [SystemMessage(content=_load_teacher_prompt())]
        messages.extend(lc_history)
        messages.append(HumanMessage(content=text))

        tools_list = _teacher_tools()
        by_name = _teacher_tools_by_name()

        def _emit(kind: str, txt: str):
            if not publish_channel_events:
                return
            s = str(txt).strip()
            if not s:
                return
            event = {
                "thinking": "teacher_thinking",
                "tool_call": "teacher_tool_call",
                "tool_out": "teacher_tool_out",
            }.get(kind, "teacher_" + kind)
            realtime.publish(user_id, event, {"text": s})

        llm_with_tools = get_llm().bind_tools(tools_list) if tools_list else get_llm()
        reply = ""
        for _step in range(_MAX_TOOL_ITERS):
            ai = llm_with_tools.invoke(messages)
            calls = getattr(ai, "tool_calls", None)
            if not calls:
                reply = ai.content or ""
                break
            if ai.content:
                _emit("thinking", ai.content)
            messages.append(ai)
            for tc in calls:
                args = tc.get("args") or {}
                arg_str = "、".join(f"{k}={v}" for k, v in args.items() if k not in ("user_id",))
                _emit("tool_call", f"调用 {tc['name']}({arg_str})")
                tool = by_name.get(tc["name"])
                if tool is None:
                    out = f"[老师不使用工具 {tc['name']}]"
                else:
                    try:
                        out = tool.invoke({**args, "user_id": user_id})
                    except Exception as e:  # noqa: BLE001
                        out = f"[工具 {tc['name']} 执行失败] {e}"
                out = str(out)
                _emit("tool_out", out[:400])
                messages.append(ToolMessage(content=out, tool_call_id=tc["id"]))
        else:
            reply = reply or "（这个问题步骤有点多，我先停下来听你的想法。）"

        reply = (reply or "").strip()

        # 落库
        stm.add_message(sid, Message(role=MessageRole.USER, content=text))
        stm.add_message(sid, Message(role=MessageRole.ASSISTANT, content=reply))

        if publish_channel_events:
            realtime.publish(user_id, "teacher_reply", {"text": reply})
        return reply

    def get_history(self, user_id: str) -> list[dict]:
        """给前端拉历史用。返回 [{role, content}] 列表。"""
        sid = _session_id(user_id)
        stm = ShortTermMemory()  # 只读，不需要 llm
        msgs = stm.get_history(sid)
        return [
            {"role": m.role.value, "content": m.content}
            for m in msgs
        ]

    def clear(self, user_id: str) -> None:
        """清空老师历史（用户主动 /清空老师记忆 时）。"""
        sid = _session_id(user_id)
        from app.core.memory.store import get_conn
        conn = get_conn()
        try:
            conn.execute("DELETE FROM stm_messages WHERE session_id=?", (sid,))
            conn.commit()
        finally:
            conn.close()


# 单例
_teacher = TeacherAgent()


def handle(user_id: str, text: str, publish_channel_events: bool = True) -> str:
    return _teacher.handle_message(user_id, text, publish_channel_events=publish_channel_events)


def history(user_id: str) -> list[dict]:
    return _teacher.get_history(user_id)


# ── 系统 Agent 注册 ────────────────────────────────────────
TEACHER_USER_ID = "system:teacher"
TEACHER_DISPLAY_NAME = "老师"
TEACHER_AGENT_NAME = "老师"


def register_in_bot_users() -> None:
    from app.core.memory.bot_users import BotUsersStore
    BotUsersStore().register_system_agent(
        user_id=TEACHER_USER_ID,
        display_name=TEACHER_DISPLAY_NAME,
        agent_name=TEACHER_AGENT_NAME,
    )
    logger.info("老师 Agent 已注册到 bot_users 表（user_id=%s）", TEACHER_USER_ID)
