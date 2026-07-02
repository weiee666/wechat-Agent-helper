# -*- coding: utf-8 -*-
"""跨 Agent 通信的授权层：Pending 授权请求 + 一次性令牌。

设计：
- 每个用户同时只能有一个 pending 授权请求（新的覆盖旧的）
- 请求 5 分钟过期
- 令牌颁发后一次性；用过或过期即销毁

存储：进程内存字典（重启即失效——重启后所有跨 Agent 对话都重新开始，这也是设计意图）。
"""
from __future__ import annotations

import secrets
import threading
import time
from dataclasses import dataclass

_TTL_PENDING = 5 * 60      # 5 分钟
_TTL_TOKEN = 5 * 60


@dataclass
class PendingAuth:
    """B 用户微信里显示"我想给 X 发 yyy"待批准的请求。"""
    id: str
    agent_user_id: str        # Agent 的用户（B），要 Ta 授权
    agent_display: str        # B 的显示名（比如"危呃呃"）
    target_user_id: str       # 消息要送达的对象（A），外部 A2A caller 为 ''
    target_display: str       # A 的显示名（比如"危博"或外部 API Key 名字）
    proposed_text: str        # B Agent 想发的话（yyy）
    origin_conversation_round: int  # A→B 是第几轮（跨 Agent 5 轮上限的一部分）
    created_at: float
    # A2A executor 关联的 future / loop（None 表示纯内部对话，无需 A2A 完成回调）
    external_loop: object | None = None
    external_future: object | None = None


_pending: dict[str, PendingAuth] = {}          # agent_user_id → PendingAuth（每用户 1 个）
_tokens: dict[str, tuple[str, str, float]] = {}  # token → (target_uid, text, expires_at)
_lock = threading.Lock()


# ── pending auth ────────────────────────────────────────────
def create_pending(agent_user_id: str, agent_display: str,
                   target_user_id: str, target_display: str,
                   proposed_text: str, conv_round: int,
                   external_loop=None, external_future=None) -> PendingAuth:
    """创建一个新 pending。若该 agent 已有旧 pending，直接覆盖（旧的丢弃）。
    external_loop / external_future 用于关联 A2A executor 的 asyncio.Future，
    resolve 时会跨线程唤醒 executor。"""
    now = time.time()
    p = PendingAuth(
        id=secrets.token_urlsafe(8),
        agent_user_id=agent_user_id,
        agent_display=agent_display,
        target_user_id=target_user_id,
        target_display=target_display,
        proposed_text=proposed_text,
        origin_conversation_round=conv_round,
        created_at=now,
        external_loop=external_loop,
        external_future=external_future,
    )
    with _lock:
        _cleanup_locked()
        _pending[agent_user_id] = p
    return p


def get_pending(agent_user_id: str) -> PendingAuth | None:
    with _lock:
        _cleanup_locked()
        return _pending.get(agent_user_id)


def clear_pending(agent_user_id: str) -> None:
    with _lock:
        _pending.pop(agent_user_id, None)


def _cleanup_locked() -> None:
    now = time.time()
    stale = [uid for uid, p in _pending.items() if now - p.created_at > _TTL_PENDING]
    for uid in stale:
        _pending.pop(uid, None)
    stale_t = [t for t, (_, _, exp) in _tokens.items() if exp < now]
    for t in stale_t:
        _tokens.pop(t, None)


# ── tokens ──────────────────────────────────────────────────
def issue_token(target_user_id: str, text: str) -> str:
    """颁发一次性令牌。使用后须 spend_token 作废。"""
    tok = secrets.token_urlsafe(16)
    with _lock:
        _tokens[tok] = (target_user_id, text, time.time() + _TTL_TOKEN)
    return tok


def spend_token(token: str) -> tuple[str, str] | None:
    """消费令牌：返回 (target_user_id, text) 并作废；无效返回 None。"""
    with _lock:
        _cleanup_locked()
        entry = _tokens.pop(token, None)
    if not entry:
        return None
    target_uid, text, _exp = entry
    return target_uid, text


# ── 意图识别（简单规则，先不上 LLM）────────────────────────
_ALLOW = {"允许", "可以", "好", "行", "同意", "发", "发吧", "发送", "yes", "y", "ok", "okay", "好的"}
_DENY = {"不允许", "不", "不行", "不同意", "不发", "别发", "拒绝", "no", "n", "算了"}


def classify_reply(text: str) -> str:
    """把 B 用户对'是否允许发送'的回复分类。
    返回 'allow' / 'deny' / 'unclear'。"""
    if not text:
        return "unclear"
    # 去空白 / 标点
    import re
    cleaned = re.sub(r"[\s，。、！？!?.,；;：:~～]+", "", text).lower()
    if not cleaned:
        return "unclear"
    if cleaned in _ALLOW:
        return "allow"
    if cleaned in _DENY:
        return "deny"
    # 允许类的前缀匹配（"允许发送"，"好的发吧"）
    if any(cleaned.startswith(a) for a in _ALLOW):
        return "allow"
    if any(cleaned.startswith(d) for d in _DENY):
        return "deny"
    return "unclear"
