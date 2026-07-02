# -*- coding: utf-8 -*-
"""主动向 bot 用户 push 消息（不通过用户先发起）。

场景：跨 Agent 对话时，target 用户本人也要在自己的微信里收到"[XX 的助手] xxx"，
以便必要时接管。

依赖：
- manager.get_user_session(user_id) 拿 bot token
- bot_users.last_ctx 拿最近一次 iLink context_token（每条来消息时保存）

无 session 或无 last_ctx 时降级为不 push（返回 False），主流程不失败。
"""
from __future__ import annotations

import logging

from app.channel import ilink
from app.core.memory.bot_users import BotUsersStore

logger = logging.getLogger(__name__)


def push_to_user(user_id: str, text: str) -> bool:
    """向 user_id 对应的微信号主动 push 一条文本。

    返回 True = 已发出；False = 无法发送（bot 离线 / 无 ctx / iLink 失败）。
    """
    if not user_id or not text:
        return False
    # 找 target 的 bot session（含 token）
    from app import manager
    session = manager.get_user_session(user_id)
    if not session:
        logger.info("dispatch: user %s 没有活跃 bot session，无法 push", user_id)
        return False
    token = session.get("token")
    if not token:
        return False
    # 找 target 的最近 context_token
    me = BotUsersStore().get(user_id)
    ctx = (me.last_ctx if me else "") or ""
    if not ctx:
        logger.info("dispatch: user %s 没有 last_ctx（可能从没跟 bot 说过话），无法 push", user_id)
        return False
    try:
        ilink.send_message(token, user_id, text, ctx)
        return True
    except Exception as e:  # noqa: BLE001
        logger.warning("dispatch: 向 %s push 失败: %s", user_id, e)
        return False
