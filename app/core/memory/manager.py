# -*- coding: utf-8 -*-
"""统一记忆管理：协调短期记忆与长期记忆。同步版（裁剪自 autoweixin）。"""
from __future__ import annotations

import logging

from app.core.memory.long_term import LongTermMemory
from app.core.memory.short_term import ShortTermMemory
from app.models.schemas import MemoryContext, Message

logger = logging.getLogger(__name__)


class MemoryManager:
    def __init__(self, short_term: ShortTermMemory, long_term: LongTermMemory) -> None:
        self._stm = short_term
        self._ltm = long_term

    def get_context(self, session_id: str, query: str) -> MemoryContext:
        """短期历史 + 长期召回，合并成一个上下文。"""
        try:
            short_msgs = self._stm.get_history(session_id)
        except Exception as e:  # noqa: BLE001
            logger.exception("读取短期记忆失败: %s", e)
            short_msgs = []
        try:
            long_items = self._ltm.recall(query, session_id, top_k=5)
        except Exception as e:  # noqa: BLE001
            logger.exception("长期记忆召回失败: %s", e)
            long_items = []
        return MemoryContext(
            session_id=session_id,
            short_term_messages=short_msgs,
            long_term_items=long_items,
        )

    def save(self, session_id: str, message: Message) -> None:
        """写入短期记忆（滑窗与压缩由 ShortTermMemory 负责）。"""
        self._stm.add_message(session_id, message)
