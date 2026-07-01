# -*- coding: utf-8 -*-
"""旁观面板一次性 token 存储（进程内存，重启即失效）。

用户在微信里向 bot 发「/看板」→ runner 调 issue() 签发 token → bot 回复带 token 的 URL。
用户打开面板 → 浏览器带 ?token=xxx 访问后端 /session/*/history 和 /pusher/auth
→ 后端 lookup() 换出 user_id 后再放行。

设计：
- 一次签发有效期 1 小时，够本地测试；到期自动清理
- 内存 dict，进程重启即失效（用户重新 /看板 即可）
- 不做多进程共享（当前是单进程 systemd 服务，够用）
"""
from __future__ import annotations

import secrets
import threading
import time

_TTL_SECONDS = 3600  # 1 小时
_tokens: dict[str, tuple[str, float]] = {}  # token -> (user_id, expires_at)
_lock = threading.Lock()


def _cleanup_locked() -> None:
    now = time.time()
    stale = [t for t, (_, exp) in _tokens.items() if exp < now]
    for t in stale:
        _tokens.pop(t, None)


def issue(user_id: str, ttl: int = _TTL_SECONDS) -> str:
    """签发一个 token 给 user_id 用。返回 token 字符串。"""
    with _lock:
        _cleanup_locked()
        token = secrets.token_urlsafe(24)
        _tokens[token] = (user_id, time.time() + ttl)
    return token


def lookup(token: str) -> str | None:
    """token 换 user_id；失效或不存在返回 None。"""
    if not token:
        return None
    with _lock:
        _cleanup_locked()
        v = _tokens.get(token)
    if not v:
        return None
    user_id, exp = v
    if exp < time.time():
        return None
    return user_id


def revoke(token: str) -> None:
    with _lock:
        _tokens.pop(token, None)
