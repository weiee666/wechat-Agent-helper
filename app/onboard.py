# -*- coding: utf-8 -*-
"""自助开通：每个访客一张全新二维码，确认后热起账号。

一个二维码 = 一次性登录令牌，只能绑一个账号。所以每个访客都得现生成一张新码、
单独轮询。这里给每个 ticket 起一个后台 worker 跑登录流程，前端轮询 ticket 状态。
"""
from __future__ import annotations

import logging
import threading
import time
import uuid

import requests

from app import config, manager
from app.channel import accounts, ilink
from app.channel.qr_web import _svg_bytes

logger = logging.getLogger("weixin-agent.onboard")

# ticket -> {status, qr_svg, account_id}
_sessions: dict[str, dict] = {}
_lock = threading.Lock()

# 单张开通会话最长存活（防止 worker 永久挂着）
_MAX_SECONDS = 600


def _set(ticket: str, **kw) -> None:
    with _lock:
        if ticket in _sessions:
            _sessions[ticket].update(kw)


def _qr_svg(content: str) -> str:
    return _svg_bytes(content).decode("utf-8")


def start_login() -> dict:
    """开一个新的开通会话，返回 {ticket, status, qr_svg}。"""
    ticket = uuid.uuid4().hex
    with _lock:
        _sessions[ticket] = {"status": "starting", "qr_svg": "", "account_id": None}
    threading.Thread(target=_worker, args=(ticket,), daemon=True, name=f"onboard-{ticket[:8]}").start()
    # 稍等首张二维码就绪再返回
    for _ in range(60):
        with _lock:
            s = _sessions[ticket]
            if s["qr_svg"] or s["status"] in ("error",):
                break
        time.sleep(0.1)
    return get_status(ticket) | {"ticket": ticket}


def get_status(ticket: str) -> dict | None:
    with _lock:
        s = _sessions.get(ticket)
        return dict(s) if s else None


def _worker(ticket: str) -> None:
    try:
        qr = ilink._get(f"ilink/bot/get_bot_qrcode?bot_type={config.ILINK_BOT_TYPE}")
        current = qr["qrcode"]
        _set(ticket, status="wait", qr_svg=_qr_svg(qr["qrcode_img_content"]))

        deadline = time.time() + _MAX_SECONDS
        while time.time() < deadline:
            try:
                st = ilink._get(
                    f"ilink/bot/get_qrcode_status?qrcode={requests.utils.quote(current)}", timeout=60
                )
            except requests.exceptions.Timeout:
                continue
            status = st.get("status")
            if status == "scaned":
                _set(ticket, status="scaned")
            elif status == "expired":
                qr = ilink._get(f"ilink/bot/get_bot_qrcode?bot_type={config.ILINK_BOT_TYPE}")
                current = qr["qrcode"]
                _set(ticket, status="wait", qr_svg=_qr_svg(qr["qrcode_img_content"]))
            elif status == "confirmed":
                logger.info("登录确认返回字段: %s", list(st.keys()))  # 核实 ilink_user_id 是否存在
                session = {
                    "token": st["bot_token"],
                    "baseUrl": st.get("baseurl") or config.ILINK_BASE_URL,
                    "accountId": st.get("ilink_bot_id"),
                    "userId": st.get("ilink_user_id"),   # 扫码者的微信用户 id，用于"同人重扫停旧 bot"
                }
                accounts.save_account(session)
                manager.add_account(session)   # 热起收消息线程（内部会停掉同一用户的旧 bot）
                _set(ticket, status="confirmed", account_id=session["accountId"], qr_svg="")
                logger.info("自助开通成功: %s (userId=%s)", session["accountId"], session.get("userId"))
                return
            time.sleep(1)
        _set(ticket, status="expired")
    except Exception as e:  # noqa: BLE001
        logger.exception("开通 worker 出错: %s", e)
        _set(ticket, status="error")
