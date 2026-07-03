# -*- coding: utf-8 -*-
"""Claude Agent：通过反向 WebSocket 连接跑在用户 Mac 上的 Claude Code CLI。

架构：
    用户 Mac                              腾讯云服务器
    ┌──────────────┐                     ┌──────────────────────┐
    │ claude_bridge│──WS 主动连接───────▶│ /a2a/agents/claude/ws│
    │  (Python)    │◀──推任务 JSON──────│ ClaudeAgent 单例      │
    │   + claude   │──推回复 JSON──────▶│ + asyncio.Future 桥   │
    └──────────────┘                     └──────────────────────┘

流程：
1. Mac daemon 启动 → 携 API Key 连服务器 WS → 服务端 mark Claude 'running'
2. 服务器 call_agent(target='Claude', message=xxx) → ClaudeAgent.handle
3. handle 创建 pending future → 通过 WS 推 task JSON
4. Mac daemon 收到 → 调 `claude -p "..."` → 拿到 stdout
5. Mac daemon 通过 WS 推 result JSON（含 task_id）
6. 服务端 resolve future → handle 拿到 reply return
7. WS 断开 → mark Claude 'offline'

调 handle 是同步的（跟 teacher.handle 一样接口），内部用 asyncio.run
把 async 桥接过去；如果已经在 async 上下文里就用 to_thread。
"""
from __future__ import annotations

import asyncio
import logging
import secrets
import time
from dataclasses import dataclass

logger = logging.getLogger(__name__)


TASK_TIMEOUT_SECONDS = 300.0  # 5 分钟等 Claude 回复


@dataclass
class _PendingTask:
    task_id: str
    future: asyncio.Future
    prompt: str
    created_at: float
    user_id: str = ""


class ClaudeAgentHub:
    """单例：管理 Mac daemon 的 WebSocket 连接 + 任务队列 + per-user session。"""

    def __init__(self):
        self._ws = None                         # 当前活跃的 WebSocket 连接对象
        self._ws_loop = None                    # WS 所在的 asyncio loop
        self._pending: dict[str, _PendingTask] = {}
        self._queue: asyncio.Queue | None = None  # 任务发送队列（在 WS loop 上）
        self._lock = None                       # asyncio.Lock（一 Mac 一 session 串行处理）
        # 每用户跟 Claude 独立 session（进程内存；断电重启即清空）
        # user_id → Claude Code CLI 返回的 session_id
        self._user_sessions: dict[str, str] = {}

    # ── WS 连接管理（FastAPI WebSocket handler 调用）──
    def attach(self, ws, loop) -> None:
        """WS 连上后调。ws 是 starlette.websockets.WebSocket。"""
        self._ws = ws
        self._ws_loop = loop
        if self._queue is None or self._queue._loop is not loop:  # type: ignore
            self._queue = asyncio.Queue()
        if self._lock is None or self._lock._loop is not loop:  # type: ignore
            self._lock = asyncio.Lock()
        # 标记 Claude 上线
        try:
            from app.core.memory.bot_users import BotUsersStore
            BotUsersStore().mark_system_online("system:claude")
        except Exception as e:  # noqa: BLE001
            logger.warning("mark Claude online 失败: %s", e)
        logger.info("Claude bridge WS 已连接")

    def detach(self) -> None:
        """WS 断开时调。"""
        self._ws = None
        # 未完成的 future 全部 fail
        for tid, p in list(self._pending.items()):
            if not p.future.done():
                p.future.set_exception(RuntimeError("Claude 端已断连"))
            self._pending.pop(tid, None)
        # 标记 offline
        try:
            from app.core.memory.bot_users import BotUsersStore
            BotUsersStore().mark_system_offline("system:claude")
        except Exception as e:  # noqa: BLE001
            logger.warning("mark Claude offline 失败: %s", e)
        logger.info("Claude bridge WS 已断开")

    def is_online(self) -> bool:
        return self._ws is not None

    # ── Mac 端发来的消息 ──
    def on_result(self, task_id: str, reply: str, session_id: str | None = None) -> None:
        """Mac 端收到任务后调 claude 返回来的结果，resolve pending future。
        session_id：Claude Code 返回的 session_id，需要记住给下次用（--resume）。"""
        p = self._pending.pop(task_id, None)
        if p is None:
            logger.warning("收到未知 task_id 的 result: %s", task_id)
            return
        # 更新该用户的 session_id
        if session_id and getattr(p, "user_id", None):
            self._user_sessions[p.user_id] = session_id
        if p.future.done():
            return
        loop = self._ws_loop
        if loop:
            loop.call_soon_threadsafe(p.future.set_result, reply)

    def on_error(self, task_id: str, error: str) -> None:
        p = self._pending.pop(task_id, None)
        if p is None:
            return
        if p.future.done():
            return
        loop = self._ws_loop
        if loop:
            loop.call_soon_threadsafe(
                p.future.set_exception, RuntimeError(f"Claude 端出错: {error}"),
            )

    # ── session 管理 ──
    def get_session(self, user_id: str) -> str | None:
        return self._user_sessions.get(user_id) if user_id else None

    def reset_session(self, user_id: str) -> None:
        """用户"清空 Claude 记忆" 时调。"""
        self._user_sessions.pop(user_id, None)

    # ── 外部调用（call_agent 特殊路径 / 看板 panel_chat）──
    async def ask_async(self, user_id: str, prompt: str) -> str:
        """异步请求 Claude 处理一段 prompt，返回 reply。
        自动带上 user_id 关联的 session_id（首次为 None，Claude 返回后记录，下次接续）。
        用 asyncio.Lock 保证一 Mac 一次一个 session（Claude CLI 不能并行）。"""
        if not self.is_online():
            raise RuntimeError("Claude 目前不在线（Mac 端 daemon 未连接）")
        if self._ws_loop is None or self._lock is None:
            raise RuntimeError("Claude Hub 未就绪")
        loop = self._ws_loop

        # 创建 future + 生成 task_id
        task_id = secrets.token_urlsafe(12)
        future: asyncio.Future = loop.create_future()
        self._pending[task_id] = _PendingTask(
            task_id=task_id, future=future, prompt=prompt, created_at=time.time(),
            user_id=user_id,
        )

        session_id = self._user_sessions.get(user_id)

        async def _do():
            async with self._lock:  # 排队串行
                ws = self._ws
                if ws is None:
                    self._pending.pop(task_id, None)
                    raise RuntimeError("Claude 已断连")
                payload = {
                    "type": "task",
                    "task_id": task_id,
                    "prompt": prompt,
                }
                if session_id:
                    payload["session_id"] = session_id
                await ws.send_json(payload)
                try:
                    return await asyncio.wait_for(future, timeout=TASK_TIMEOUT_SECONDS)
                except asyncio.TimeoutError:
                    self._pending.pop(task_id, None)
                    raise RuntimeError(f"Claude 超时（{TASK_TIMEOUT_SECONDS:.0f} 秒未响应）") from None

        # 如果当前调用者已在 ws_loop 上运行 → 直接 await；否则 threadsafe run
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        if running is loop:
            return await _do()
        # 从其他 loop / 同步线程 → 用 run_coroutine_threadsafe
        cf = asyncio.run_coroutine_threadsafe(_do(), loop)
        # 同步等待
        return await asyncio.wrap_future(cf)

    def ask_sync(self, user_id: str, prompt: str) -> str:
        """同步接口（供 call_agent 从 handle_agent_message 调用）。"""
        if self._ws_loop is None:
            raise RuntimeError("Claude Hub 未就绪")
        cf = asyncio.run_coroutine_threadsafe(self.ask_async(user_id, prompt), self._ws_loop)
        return cf.result(timeout=TASK_TIMEOUT_SECONDS + 5)


# 单例
hub = ClaudeAgentHub()


# 系统 Agent 标识
CLAUDE_USER_ID = "system:claude"
CLAUDE_DISPLAY_NAME = "Claude"
CLAUDE_AGENT_NAME = "Claude"


def register_in_bot_users() -> None:
    """把 Claude 注册到 bot_users 表。启动时调一次。
    注册时 status 默认 offline —— 等 Mac daemon WS 连接上后再置 running。"""
    from app.core.memory.bot_users import BotUsersStore
    BotUsersStore().register_system_agent(
        user_id=CLAUDE_USER_ID,
        display_name=CLAUDE_DISPLAY_NAME,
        agent_name=CLAUDE_AGENT_NAME,
    )
    # 初始离线（等 daemon 连上）
    BotUsersStore().mark_system_offline(CLAUDE_USER_ID)
    logger.info("Claude Agent 已注册（等 Mac daemon 上线）")


def _stm_session_id(user_id: str) -> str:
    return f"claude:{user_id}"


def handle(user_id: str, message: str) -> str:
    """同步入口（tools.call_agent 特殊路径 / panel_chat 调用）。
    per-user session 自动关联（第一次问 Claude 创建 session，后续用 --resume 接续）。
    落 SQLite：session_id=claude:{uid}，方便看板拉历史。"""
    from app.core.memory.short_term import ShortTermMemory
    from app.models.enums import MessageRole
    from app.models.schemas import Message

    reply = hub.ask_sync(user_id, message)
    try:
        sid = _stm_session_id(user_id)
        stm = ShortTermMemory()
        stm.add_message(sid, Message(role=MessageRole.USER, content=message))
        stm.add_message(sid, Message(role=MessageRole.ASSISTANT, content=reply or ""))
    except Exception as e:  # noqa: BLE001
        logger.warning("Claude 历史落库失败: %s", e)
    return reply


def history(user_id: str) -> list[dict]:
    """给看板拉历史用。返回 [{role, content}] 列表。"""
    from app.core.memory.short_term import ShortTermMemory
    msgs = ShortTermMemory().get_history(_stm_session_id(user_id))
    return [{"role": m.role.value, "content": m.content} for m in msgs]


def clear(user_id: str) -> None:
    """清空 Claude 历史（用户主动清空时）。"""
    from app.core.memory.store import get_conn
    conn = get_conn()
    try:
        conn.execute("DELETE FROM stm_messages WHERE session_id=?", (_stm_session_id(user_id),))
        conn.commit()
    finally:
        conn.close()
    hub.reset_session(user_id)
