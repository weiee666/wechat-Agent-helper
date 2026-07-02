# -*- coding: utf-8 -*-
"""跨 Agent 对话的进程内状态（用于 5 轮限制 + 起源 user 追踪）。

Agent 间对话是"同步递归"：
  用户 A → runner.handle_text → LLM → 工具 call_agent → runner.handle_agent_message (B)
        → B 的 LLM → 可能又调 call_agent → handle_agent_message (A) → ...

因为都在同一个 Python 线程（poller 单线程处理该用户的消息），用 threading.local
存计数器就够；同一"起源消息"的整个链条共享同一 counter。

reset() 在 poller 每次开始处理新的用户消息时调用；inc_round() 在 call_agent 工具入口调。
"""
from __future__ import annotations

import threading

MAX_ROUNDS = 5

_state = threading.local()


def reset() -> None:
    """poller 处理新用户消息时清零。"""
    _state.round = 0
    _state.origin_user_id = None
    _state.reply_pushed = False


def mark_reply_pushed() -> None:
    """call_agent 已把对方原话 push 到 sender 微信 —— 起源 Agent 不用再输出。"""
    _state.reply_pushed = True


def was_reply_pushed() -> bool:
    return getattr(_state, "reply_pushed", False)


def set_origin(user_id: str) -> None:
    _state.origin_user_id = user_id


def get_origin() -> str | None:
    return getattr(_state, "origin_user_id", None)


def get_round() -> int:
    return getattr(_state, "round", 0)


def inc_round() -> int:
    """+1 并返回新值。call_agent 工具入口调用；返回 > MAX_ROUNDS 表示应拒绝。"""
    _state.round = getattr(_state, "round", 0) + 1
    return _state.round
