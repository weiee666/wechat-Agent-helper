# -*- coding: utf-8 -*-
"""iLink Bot API 客户端：鉴权 / 扫码登录 / 长轮询收 / 发消息。

渠道适配层——只负责微信侧收发，不含业务逻辑。端点与字段对照官方协议。
"""
from __future__ import annotations

import base64
import json
import secrets
import time
import uuid

import qrcode
import requests

from app import config

_BASE = config.ILINK_BASE_URL


# ── 鉴权 header ──────────────────────────────────────────────
def _wechat_uin() -> str:
    """X-WECHAT-UIN：随机 uint32 → 十进制字符串 → base64（每次变，防重放）。"""
    return base64.b64encode(str(secrets.randbits(32)).encode()).decode()


def _headers(token: str | None = None) -> dict:
    h = {
        "Content-Type": "application/json",
        "AuthorizationType": "ilink_bot_token",
        "X-WECHAT-UIN": _wechat_uin(),
    }
    if token:
        h["Authorization"] = f"Bearer {token}"
    return h


def _get(path: str, timeout: int = 20) -> dict:
    r = requests.get(f"{_BASE}/{path}", timeout=timeout)
    r.raise_for_status()
    return r.json()


def _post(endpoint: str, body: dict, token: str, timeout: int = 40) -> dict | None:
    payload = {**body, "base_info": {"channel_version": config.ILINK_CHANNEL_VERSION}}
    try:
        r = requests.post(f"{_BASE}/{endpoint}", headers=_headers(token),
                          data=json.dumps(payload), timeout=timeout)
        r.raise_for_status()
        return r.json()
    except requests.exceptions.Timeout:
        return None  # 长轮询正常超时


# ── 登录态持久化 ─────────────────────────────────────────────
def load_session() -> dict | None:
    f = config.ILINK_TOKEN_FILE
    if f.exists():
        return json.loads(f.read_text(encoding="utf-8"))
    return None


def _save_session(s: dict) -> None:
    config.ILINK_TOKEN_FILE.write_text(json.dumps(s, indent=2, ensure_ascii=False), encoding="utf-8")
    try:
        config.ILINK_TOKEN_FILE.chmod(0o600)
    except OSError:
        pass


def _print_qr(content: str) -> None:
    qr = qrcode.QRCode(border=1)
    qr.add_data(content)
    qr.make(fit=True)
    qr.print_ascii(invert=True)


def login(qr_web: bool = False) -> dict:
    """扫码登录，返回并持久化 session。

    qr_web=True：额外起一个临时网页显示二维码（headless 服务器用，浏览器打开扫）。
    """
    print("🔐 获取登录二维码...")
    qr = _get(f"ilink/bot/get_bot_qrcode?bot_type={config.ILINK_BOT_TYPE}")
    current = qr["qrcode"]

    web = None
    if qr_web:
        from app.channel.qr_web import QRWebServer, local_ip
        web = QRWebServer(qr["qrcode_img_content"], port=config.ILINK_QR_WEB_PORT)
        web.start()
        print(f"\n🌐 二维码网页已开：在浏览器打开  http://{local_ip()}:{web.port}/")
        print(f"   （云服务器请用公网IP，如 http://43.142.81.135:{web.port}/，并放行该端口）\n")
    print("📱 也可扫描下面终端里的二维码：\n")
    _print_qr(qr["qrcode_img_content"])

    try:
        # 无超时：一直等用户扫码，二维码过期就自动换新码（适合发给用户慢慢扫）。
        while True:
            # get_qrcode_status 是挂起等待型接口，给 60s；超时只代表"还没扫"，继续。
            try:
                st = _get(f"ilink/bot/get_qrcode_status?qrcode={requests.utils.quote(current)}", timeout=60)
            except requests.exceptions.Timeout:
                continue
            status = st.get("status")
            if status == "wait":
                print(".", end="", flush=True)
            elif status == "scaned":
                print("\n👀 已扫码，请在手机上确认...")
            elif status == "expired":
                print("\n⏳ 二维码过期，已自动刷新，请重新扫描…")
                qr = _get(f"ilink/bot/get_bot_qrcode?bot_type={config.ILINK_BOT_TYPE}")
                current = qr["qrcode"]
                if web:
                    web.update(qr["qrcode_img_content"])  # 网页自动刷新成新码
                _print_qr(qr["qrcode_img_content"])
            elif status == "confirmed":
                session = {
                    "token": st["bot_token"],
                    "baseUrl": st.get("baseurl") or _BASE,
                    "accountId": st.get("ilink_bot_id"),
                    "userId": st.get("ilink_user_id"),   # 扫码者微信用户 id
                }
                from app.channel import accounts
                accounts.save_account(session)
                print(f"\n✅ 登录成功！Bot ID: {session['accountId']}")
                return session
            time.sleep(1)
    finally:
        if web:
            web.stop()


# ── 收发消息 ─────────────────────────────────────────────────
def get_updates(token: str, buf: str) -> dict:
    resp = _post("ilink/bot/getupdates", {"get_updates_buf": buf}, token, timeout=40)
    return resp or {"ret": 0, "msgs": [], "get_updates_buf": buf}


def send_message(token: str, to_user_id: str, text: str, context_token: str) -> None:
    _post(
        "ilink/bot/sendmessage",
        {
            "msg": {
                "from_user_id": "",
                "to_user_id": to_user_id,
                "client_id": f"wa-{uuid.uuid4()}",
                "message_type": 2,
                "message_state": 2,
                "context_token": context_token,
                "item_list": [{"type": 1, "text_item": {"text": text}}],
            }
        },
        token,
    )
