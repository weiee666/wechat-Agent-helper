# -*- coding: utf-8 -*-
"""应用级枚举（裁剪自 autoweixin）。"""
from enum import Enum


class MessageRole(str, Enum):
    """对话消息角色。"""

    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"
    TOOL = "tool"


class TaskStatus(str, Enum):
    """结构化任务状态。"""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
