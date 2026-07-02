# -*- coding: utf-8 -*-
"""老师 Agent：跨用户共享的教学助手。

设计要点：
- **每用户对话独立**：不同用户跟老师的对话互不可见
- **内存 session**：对话 buffer 存进程内存 dict，避免每轮都写库
- **延迟落库**：15 分钟无新对话 → summarize 到长期记忆（Phase 1 简化：先不做）
- **工具**：web_search + structure_task（帮用户记要点）
- **realtime 事件**：老师的思考/工具调用/回复用 teacher_* 事件推给用户面板，
  这样用户面板能看到老师的思考过程（默认折叠，跟自己助手一样）

跟 VoiceTaskAgent 的对比：
- VoiceTaskAgent 每用户一份 Agent（不同 memory namespace）
- TeacherAgent 单例，多用户共享逻辑但每用户独立 session state
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from app import config, realtime
from app.agent import tools as agent_tools
from app.agent.llm import get_llm

logger = logging.getLogger(__name__)

_MAX_TOOL_ITERS = 5
_TEACHER_SESSION_TTL = 15 * 60  # 15 分钟无对话即"过期"（TODO Phase 后期用作触发 summarize）

# 老师只能用这些工具（call_agent 特意不给——老师不主动打扰其他用户 Agent）
_TEACHER_TOOL_NAMES = ("web_search", "structure_task")


@dataclass
class TeacherSession:
    user_id: str
    messages: list = field(default_factory=list)  # LangChain BaseMessage 列表（不含 system）
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)


_sessions: dict[str, TeacherSession] = {}
_sessions_lock = threading.Lock()


def _load_teacher_prompt() -> str:
    return (config.PROMPT_DIR / "teacher_system.txt").read_text(encoding="utf-8")


def _teacher_tools() -> list:
    return [t for t in agent_tools.TOOLS if t.name in _TEACHER_TOOL_NAMES]


def _teacher_tools_by_name() -> dict:
    return {t.name: t for t in _teacher_tools()}


def get_or_create_session(user_id: str) -> TeacherSession:
    with _sessions_lock:
        s = _sessions.get(user_id)
        if s is None:
            s = TeacherSession(user_id=user_id)
            _sessions[user_id] = s
        return s


def touch_session(user_id: str) -> None:
    with _sessions_lock:
        s = _sessions.get(user_id)
        if s:
            s.updated_at = time.time()


def clear_session(user_id: str) -> None:
    """用户主动清空老师会话（比如 /清空老师记忆）。"""
    with _sessions_lock:
        _sessions.pop(user_id, None)


class TeacherAgent:
    """老师 Agent 单例。每次 handle_message 找 user 的 session，跑 LLM tool loop。"""

    def handle_message(self, user_id: str, text: str) -> str:
        """用户对老师说 text，返回老师的 reply。
        中间过程通过 realtime 推 teacher_thinking / teacher_tool_call / teacher_tool_out 事件到用户 channel。"""
        text = (text or "").strip()
        if not text:
            return "（我在。你想问什么？）"

        # 推 user 消息事件（前端渲染需要）
        realtime.publish(user_id, "teacher_user_message", {"text": text})

        session = get_or_create_session(user_id)
        with _sessions_lock:
            session.messages.append(HumanMessage(content=text))
            history = list(session.messages)  # copy

        # 组装 LLM messages
        messages = [SystemMessage(content=_load_teacher_prompt())]
        messages.extend(history)

        # 工具（有限集）
        tools_list = _teacher_tools()
        by_name = _teacher_tools_by_name()

        def _emit(kind: str, txt: str):
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
                        # 注入 user_id：structure_task/web_search 都需要
                        out = tool.invoke({**args, "user_id": user_id})
                    except Exception as e:  # noqa: BLE001
                        out = f"[工具 {tc['name']} 执行失败] {e}"
                out = str(out)
                _emit("tool_out", out[:400])
                messages.append(ToolMessage(content=out, tool_call_id=tc["id"]))
        else:
            reply = reply or "（这个问题步骤有点多，我先停下来听你的想法。）"

        reply = (reply or "").strip()

        # 保存 AI 消息到 session
        with _sessions_lock:
            session.messages.append(AIMessage(content=reply))
            session.updated_at = time.time()

        realtime.publish(user_id, "teacher_reply", {"text": reply})
        return reply

    def get_history(self, user_id: str) -> list[dict]:
        """给前端拉最近对话历史用。返回 [{role, content}] 列表。"""
        session = _sessions.get(user_id)
        if not session:
            return []
        out = []
        for m in session.messages:
            if isinstance(m, HumanMessage):
                role = "user"
            elif isinstance(m, AIMessage):
                role = "assistant"
            else:
                role = "system"
            out.append({"role": role, "content": m.content})
        return out


# 单例
_teacher = TeacherAgent()


def handle(user_id: str, text: str) -> str:
    """入口函数，供 API 层调用。"""
    return _teacher.handle_message(user_id, text)


def history(user_id: str) -> list[dict]:
    return _teacher.get_history(user_id)
