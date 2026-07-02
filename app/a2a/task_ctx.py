# -*- coding: utf-8 -*-
"""A2A executor 与内部 bus 之间的 async 桥梁。

executor.execute() 是 async；handle_agent_message 是同步；resolve_authorization
在 poller 线程里被同步调用。当外部 A2A task 触发 handle_agent_message → 内部
调 bus.request_authorization 时，需要把该 A2A task 的 future 关联到 pending_auth，
后续 resolve_authorization 时 set_result（跨线程用 loop.call_soon_threadsafe）。

用 contextvars（也支持在 to_thread 里读到）保存当前"正在处理的 A2A task"。
"""
from __future__ import annotations

import asyncio
import contextvars
from dataclasses import dataclass


@dataclass
class ExternalTaskContext:
    loop: asyncio.AbstractEventLoop
    future: asyncio.Future
    sender_display: str
    target_user_id: str


_current: contextvars.ContextVar[ExternalTaskContext | None] = contextvars.ContextVar(
    "a2a_current_external_task", default=None,
)


def set_current(ctx: ExternalTaskContext) -> contextvars.Token:
    return _current.set(ctx)


def get_current() -> ExternalTaskContext | None:
    return _current.get()


def reset(token: contextvars.Token) -> None:
    _current.reset(token)
