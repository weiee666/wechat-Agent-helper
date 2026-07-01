# -*- coding: utf-8 -*-
"""单个 bot 账号的收消息循环。多租户：每个账号一个线程跑一个 poll_account。"""
from __future__ import annotations

import logging
import threading
import time

from app.agent.runner import VoiceTaskAgent
from app.channel import ilink

logger = logging.getLogger("weixin-agent.poller")

_agent = VoiceTaskAgent()


def _build_reply(msg: dict, token: str) -> tuple[str, bool]:
    """返回 (回复文本, 是否发送)。录制中途累积时不发送（静默不打扰）。
    详细模式下，中间过程通过 emit 实时推给微信（用同一 context_token 连发）。"""
    frm = msg.get("from_user_id") or "default"
    ctx = msg.get("context_token")

    def emit(text):
        ilink.send_message(token, frm, text, ctx)

    # 用户主键 = from_user_id（微信号），稳定；bot 的 account_id 只用于发消息，不参与数据隔离
    result = _agent.handle_ilink_message(msg, from_user_id=frm, emit=emit)
    if result is None:
        return "（没取到文字内容。若发的是语音且无自带转写，需要再接 STT。）", True
    return result.reply, result.send


def poll_account(session: dict, stop_event: threading.Event) -> None:
    """长轮询收某账号的消息，处理后回复。token 过期则结束该账号的循环。"""
    token = session["token"]
    account_id = session.get("accountId") or "default"
    logger.info("账号 %s 开始收消息", account_id)

    buf = ""
    while not stop_event.is_set():
        try:
            resp = ilink.get_updates(token, buf)
            if resp.get("get_updates_buf"):
                buf = resp["get_updates_buf"]
            for msg in resp.get("msgs") or []:
                if msg.get("message_type") != 1:
                    continue
                frm = msg.get("from_user_id")
                ctx = msg.get("context_token")
                logger.info("[%s] 收到 %s 的消息，处理中", account_id, frm)
                try:
                    reply, send = _build_reply(msg, token)
                except Exception as e:  # noqa: BLE001
                    reply, send = f"⚠️ 处理出错：{e}", True
                    logger.exception("[%s] 处理消息失败", account_id)
                if send and reply:
                    ilink.send_message(token, frm, reply, ctx)
                else:
                    logger.info("[%s] 录制中累积，静默不回复", account_id)
        except Exception as e:  # noqa: BLE001
            m = str(e)
            if "session timeout" in m or "-14" in m:
                logger.error("账号 %s 登录已过期，停止该账号循环（需重新扫码）", account_id)
                break
            logger.warning("账号 %s 轮询出错: %s，3s 后重试", account_id, m)
            time.sleep(3)
    logger.info("账号 %s 循环结束", account_id)
