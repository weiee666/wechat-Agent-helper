# -*- coding: utf-8 -*-
"""内部数据模型（记忆 / 消息）。裁剪自 autoweixin app/models/schemas.py。"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from app.models.enums import MessageRole


class Message(BaseModel):
    """对话消息（记忆内部使用，含角色枚举）。"""

    role: MessageRole
    content: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class MemoryItem(BaseModel):
    """长期记忆召回条目。"""

    id: str
    content: str
    score: float = 0.0
    metadata: dict[str, Any] = Field(default_factory=dict)


class MemoryContext(BaseModel):
    """短期 + 长期记忆合并上下文。"""

    session_id: str
    short_term_messages: list[Message] = Field(default_factory=list)
    long_term_items: list[MemoryItem] = Field(default_factory=list)
