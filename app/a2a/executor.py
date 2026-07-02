# -*- coding: utf-8 -*-
"""WeixinAgentExecutor：A2A 层调 kernel/bus 完成"外部 Agent 找我们的 Agent 说话"整套流程。

流程：
1. execute(context, event_queue)
2. 从 context 拿到 client 发来的 message.text
3. 调 bus.deliver_handshake → target 微信收到握手
4. asyncio.to_thread(handle_agent_message)（内部同步）→ B Agent 处理 → 请求授权
   （request_authorization 通过 task_ctx 感知到 external future，关联到 pending）
5. publish TaskStatusUpdateEvent(WORKING) 让 client 知道处理中
6. await future（wait_for 超时 5 min）
7. B 用户在微信回复"允许" → bus.resolve_authorization → 我们的 future set_result
8. publish 完成 message + TaskStatusUpdateEvent(COMPLETED)
   否则 publish FAILED
"""
from __future__ import annotations

import asyncio
import logging

from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.types import (
    Message,
    Part,
    Role,
    Task,
    TaskState,
    TaskStatus,
    TaskStatusUpdateEvent,
)
from google.protobuf import timestamp_pb2

from app.a2a import task_ctx
from app.a2a.task_ctx import ExternalTaskContext
from app.core.memory.bot_users import BotUsersStore

logger = logging.getLogger(__name__)

TASK_TIMEOUT_SECONDS = 300.0  # 5 分钟等 target 用户授权


def _text_from_message(msg) -> str:
    """从 A2A Message 里抽 text（合并所有 text part）。"""
    if not msg or not msg.parts:
        return ""
    chunks = []
    for p in msg.parts:
        if p.text:
            chunks.append(p.text)
    return "\n".join(chunks).strip()


def _make_status_event(task_id: str, context_id: str, state: int,
                       message_text: str = "") -> TaskStatusUpdateEvent:
    ev = TaskStatusUpdateEvent()
    ev.task_id = task_id
    ev.context_id = context_id
    status = TaskStatus()
    status.state = state
    if message_text:
        msg = Message()
        msg.role = Role.ROLE_AGENT
        part = Part()
        part.text = message_text
        msg.parts.append(part)
        status.message.CopyFrom(msg)
    ts = timestamp_pb2.Timestamp()
    ts.GetCurrentTime()
    status.timestamp.CopyFrom(ts)
    ev.status.CopyFrom(status)
    return ev


def _make_completion_message(task_id: str, context_id: str, reply: str,
                             agent_display: str) -> Message:
    """A 完成 task 时给 client 的最终 message。"""
    m = Message()
    m.message_id = f"reply-{task_id}"
    m.context_id = context_id
    m.task_id = task_id
    m.role = Role.ROLE_AGENT
    p = Part()
    p.text = f"{agent_display}：\n{reply}"
    m.parts.append(p)
    return m


class WeixinAgentExecutor(AgentExecutor):
    """服务某个具体 user_id 的 Agent。每个 (client, target_uid) 请求一个实例即可。"""

    def __init__(self, target_user_id: str, caller_display: str = "外部 Agent"):
        self.target_user_id = target_user_id
        self.caller_display = caller_display

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        task_id = context.task_id or ""
        context_id = context.context_id or ""
        message = context.message

        text = _text_from_message(message)
        if not text:
            await event_queue.enqueue_event(
                _make_status_event(task_id, context_id,
                                   TaskState.TASK_STATE_FAILED,
                                   message_text="request message empty")
            )
            return

        # 找 target
        users = BotUsersStore()
        target = users.get(self.target_user_id)
        if target is None:
            await event_queue.enqueue_event(
                _make_status_event(task_id, context_id,
                                   TaskState.TASK_STATE_FAILED,
                                   message_text=f"No agent for {self.target_user_id}")
            )
            return
        if target.status != "running":
            await event_queue.enqueue_event(
                _make_status_event(task_id, context_id,
                                   TaskState.TASK_STATE_FAILED,
                                   message_text=f"Agent for {self.target_user_id} is offline")
            )
            return

        # 先 enqueue Task 对象（SDK 要求 TaskStatusUpdateEvent 前必须有 Task）
        task = Task()
        task.id = task_id
        task.context_id = context_id
        initial_status = TaskStatus()
        initial_status.state = TaskState.TASK_STATE_SUBMITTED
        ts = timestamp_pb2.Timestamp(); ts.GetCurrentTime()
        initial_status.timestamp.CopyFrom(ts)
        task.status.CopyFrom(initial_status)
        await event_queue.enqueue_event(task)

        # publish WORKING（进入处理）
        await event_queue.enqueue_event(
            _make_status_event(task_id, context_id, TaskState.TASK_STATE_WORKING,
                               message_text="Received; running internal handshake and authorization")
        )

        # 触发内部握手
        from app.kernel import bus
        handshake_ok = bus.deliver_handshake(
            from_user_id="", from_display=self.caller_display,
            to_user_id=self.target_user_id, message=text,
        )
        if not handshake_ok:
            await event_queue.enqueue_event(
                _make_status_event(task_id, context_id, TaskState.TASK_STATE_FAILED,
                                   message_text=f"cannot deliver to {self.target_user_id}: no ctx or offline")
            )
            return

        # 触发 B Agent 处理（同步 LangChain LLM，用 to_thread 避免阻塞）
        # 关联 asyncio.Future 让 resolve_authorization 唤醒我们
        loop = asyncio.get_running_loop()
        future: asyncio.Future = loop.create_future()
        ctx = ExternalTaskContext(loop=loop, future=future,
                                  sender_display=self.caller_display,
                                  target_user_id=self.target_user_id)
        token = task_ctx.set_current(ctx)

        from app.agent.runner import VoiceTaskAgent

        def _run_sync():
            agent = VoiceTaskAgent()
            agent.handle_agent_message(
                target_user_id=self.target_user_id,
                from_user_id="",
                from_display_name=self.caller_display,
                message=text,
            )

        try:
            # 同步逻辑跑在线程里；task_ctx 是 contextvars.ContextVar，能被 to_thread 传播
            await asyncio.to_thread(_run_sync)
        finally:
            task_ctx.reset(token)

        # 现在 handle_agent_message 已经调用了 bus.request_authorization
        # future 会等 B 用户在微信里回复"允许"后 set_result
        try:
            reply = await asyncio.wait_for(future, timeout=TASK_TIMEOUT_SECONDS)
        except asyncio.TimeoutError:
            await event_queue.enqueue_event(
                _make_status_event(task_id, context_id, TaskState.TASK_STATE_FAILED,
                                   message_text=f"timeout waiting for {target.display_name or self.target_user_id} to approve")
            )
            return
        except RuntimeError as e:
            # target denied
            await event_queue.enqueue_event(
                _make_status_event(task_id, context_id, TaskState.TASK_STATE_REJECTED,
                                   message_text=f"denied: {e}")
            )
            return

        # 授权通过，返回 reply
        agent_sig = target.display_name or self.target_user_id
        if target.agent_name:
            agent_sig = f"{target.display_name}的助手 {target.agent_name}"
        else:
            agent_sig = f"{target.display_name}的助手"
        msg = _make_completion_message(task_id, context_id, reply, agent_sig)
        await event_queue.enqueue_event(msg)
        await event_queue.enqueue_event(
            _make_status_event(task_id, context_id, TaskState.TASK_STATE_COMPLETED)
        )

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        task_id = context.task_id or ""
        context_id = context.context_id or ""
        # 我们没有中断内部 handle_agent_message 的能力（同步 LLM）；只 publish CANCELED
        await event_queue.enqueue_event(
            _make_status_event(task_id, context_id, TaskState.TASK_STATE_CANCELED,
                               message_text="canceled by client")
        )
