# -*- coding: utf-8 -*-
"""看板认证 token：HMAC-SHA256 签名的紧凑 JWT，无服务端状态。

- SECRET：优先 env DASHBOARD_JWT_SECRET；否则用 secrets/dashboard_jwt.key（首次自动生成）
- token 结构：base64url(header).base64url(payload).base64url(signature)
  payload = {"uid": <user_id>, "iat": <签发时>, "exp": <过期时>}
- 服务器重启 SECRET 不变 → 已发 token 继续有效
- 因为无服务端记录，无法撤销单条 token；如需强制作废全部，改 SECRET 即可

滑动续期由 API 层负责：/session/{uid}/config 每次返回新 token（refresh_token），
前端 localStorage 覆盖，等效"访问一次续 7 天"。
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from pathlib import Path

from app import config

DEFAULT_TTL_SECONDS = 7 * 24 * 3600  # 7 天
REFRESH_THRESHOLD_SECONDS = 24 * 3600  # 剩余 < 1 天视为该刷新

_SECRETS_DIR = config.BASE_DIR / "secrets"
_KEY_FILE = _SECRETS_DIR / "dashboard_jwt.key"


def _load_or_create_secret() -> bytes:
    env = os.getenv("DASHBOARD_JWT_SECRET", "").strip()
    if env:
        return env.encode("utf-8")
    _SECRETS_DIR.mkdir(parents=True, exist_ok=True)
    if _KEY_FILE.exists():
        return _KEY_FILE.read_bytes().strip()
    fresh = secrets.token_urlsafe(48).encode("utf-8")
    _KEY_FILE.write_bytes(fresh)
    try:
        os.chmod(_KEY_FILE, 0o600)
    except OSError:
        pass
    return fresh


_SECRET = _load_or_create_secret()


def _b64u_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64u_decode(s: str) -> bytes:
    pad = "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(s + pad)


def _sign(msg: bytes) -> bytes:
    return hmac.new(_SECRET, msg, hashlib.sha256).digest()


def issue(user_id: str, ttl: int = DEFAULT_TTL_SECONDS) -> str:
    """签发一个 JWT。返回紧凑 base64url 字符串。"""
    now = int(time.time())
    header = {"alg": "HS256", "typ": "JWT"}
    payload = {"uid": user_id, "iat": now, "exp": now + ttl}
    h_b64 = _b64u_encode(json.dumps(header, separators=(",", ":")).encode("utf-8"))
    p_b64 = _b64u_encode(json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8"))
    signing_input = f"{h_b64}.{p_b64}".encode("ascii")
    sig = _b64u_encode(_sign(signing_input))
    return f"{h_b64}.{p_b64}.{sig}"


def lookup(token: str) -> str | None:
    """验签 + exp 检查，返回 user_id；不合法/过期返回 None。"""
    if not token:
        return None
    try:
        h_b64, p_b64, sig_b64 = token.split(".")
    except ValueError:
        return None
    signing_input = f"{h_b64}.{p_b64}".encode("ascii")
    expected = _sign(signing_input)
    try:
        actual = _b64u_decode(sig_b64)
    except Exception:  # noqa: BLE001
        return None
    if not hmac.compare_digest(expected, actual):
        return None
    try:
        payload = json.loads(_b64u_decode(p_b64))
    except Exception:  # noqa: BLE001
        return None
    if not isinstance(payload, dict):
        return None
    uid = payload.get("uid")
    exp = payload.get("exp")
    if not uid or not isinstance(exp, (int, float)):
        return None
    if exp < time.time():
        return None
    return str(uid)


def should_refresh(token: str) -> bool:
    """token 剩余寿命 < REFRESH_THRESHOLD_SECONDS 时提示刷新。"""
    if not token:
        return False
    try:
        _, p_b64, _ = token.split(".")
        payload = json.loads(_b64u_decode(p_b64))
        exp = payload.get("exp", 0)
    except Exception:  # noqa: BLE001
        return False
    return (exp - time.time()) < REFRESH_THRESHOLD_SECONDS


def revoke(token: str) -> None:
    """JWT 无状态，单条无法撤销；此接口保留兼容，实际是 no-op。
    如需强制作废所有 token，删除 secrets/dashboard_jwt.key 让服务重启后生成新 SECRET。"""
    _ = token
