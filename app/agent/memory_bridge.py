# -*- coding: utf-8 -*-
"""记忆桥接：把 core 的短期 + 长期记忆接进 agent，对外暴露同步函数。

—— 对应 autoweixin app/agent/memory_bridge.py ——
不同点：SQLite 后端、纯同步，去掉了 Redis / Milvus / 本地 embedding / 专属事件循环。
短期记忆的"超长摘要压缩"复用同一个 DeepSeek 模型（LangChain，invoke）。
"""
from __future__ import annotations

from app import config
from app.agent.llm import get_llm
from app.core.memory.long_term import LongTermMemory
from app.core.memory.manager import MemoryManager
from app.core.memory.short_term import ShortTermMemory
from app.models.enums import MessageRole
from app.models.schemas import MemoryContext, Message

_manager: MemoryManager | None = None


def _get_manager() -> MemoryManager:
    global _manager
    if _manager is None:
        stm = ShortTermMemory(
            llm=get_llm(),
            window_size=config.STM_WINDOW_SIZE,
            max_tokens=config.STM_MAX_TOKENS,
        )
        ltm = LongTermMemory()
        _manager = MemoryManager(stm, ltm)
    return _manager


def get_context(session_id: str, query: str) -> MemoryContext:
    """取该会话的短期历史 + 长期召回。"""
    return _get_manager().get_context(session_id, query)


def save_turn(session_id: str, role: MessageRole, content: str) -> None:
    """把一轮消息写入短期记忆（触发滑窗/压缩）。"""
    _get_manager().save(session_id, Message(role=role, content=content))


def store_longterm(session_id: str, content: str, metadata: dict | None = None) -> str:
    """把一条事实/任务写入长期记忆。"""
    return _get_manager()._ltm.store(session_id, content, metadata)
