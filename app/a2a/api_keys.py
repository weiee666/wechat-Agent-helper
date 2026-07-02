# -*- coding: utf-8 -*-
"""A2A API Key 认证：外部 Agent 通过 X-API-Key header 调用我们的 A2A 端点。

Key 本身不存数据库，只存 SHA-256 hash（丢库了 key 也不泄露）。
- `allowed_agents = '*'`  → 这把 key 能调任何内部 user 的 Agent
- `allowed_agents = 'weibo,weiee'` → 只能调这两个用户的 Agent
"""
from __future__ import annotations

import hashlib
import secrets
import time
from dataclasses import dataclass

from app.core.memory.store import get_conn


def _hash_key(key: str) -> str:
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def _now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


@dataclass
class ApiKeyMeta:
    key_hash: str
    key_name: str
    allowed_agents: str  # '*' 或逗号分隔的 user_id
    created_at: str
    last_used: str


class ApiKeyStore:
    def issue(self, name: str, allowed_agents: str = "*") -> str:
        """签发一把新 API Key，返回明文（只此一次能看到）。"""
        key = secrets.token_urlsafe(32)
        kh = _hash_key(key)
        conn = get_conn()
        try:
            conn.execute(
                "INSERT INTO api_keys(key_hash, key_name, allowed_agents, created_at)"
                " VALUES (?,?,?,?)",
                (kh, name.strip(), allowed_agents.strip() or "*", _now()),
            )
            conn.commit()
        finally:
            conn.close()
        return key

    def revoke(self, key_name: str) -> int:
        conn = get_conn()
        try:
            cur = conn.execute("DELETE FROM api_keys WHERE key_name=?", (key_name,))
            conn.commit()
            return cur.rowcount
        finally:
            conn.close()

    def verify(self, key: str, requested_agent_user_id: str = "") -> ApiKeyMeta | None:
        """校验 key 是否有效且允许访问指定 agent。返回 meta；无效返回 None。"""
        if not key:
            return None
        kh = _hash_key(key)
        conn = get_conn()
        try:
            row = conn.execute("SELECT * FROM api_keys WHERE key_hash=?", (kh,)).fetchone()
            if not row:
                return None
            # 更新 last_used
            conn.execute("UPDATE api_keys SET last_used=? WHERE key_hash=?", (_now(), kh))
            conn.commit()
        finally:
            conn.close()
        allowed = row["allowed_agents"]
        if requested_agent_user_id and allowed != "*":
            allow_set = {a.strip() for a in allowed.split(",") if a.strip()}
            if requested_agent_user_id not in allow_set:
                return None
        return ApiKeyMeta(
            key_hash=row["key_hash"],
            key_name=row["key_name"],
            allowed_agents=allowed,
            created_at=row["created_at"],
            last_used=row["last_used"],
        )

    def list_all(self) -> list[ApiKeyMeta]:
        conn = get_conn()
        try:
            rows = conn.execute("SELECT * FROM api_keys ORDER BY created_at DESC").fetchall()
        finally:
            conn.close()
        return [ApiKeyMeta(
            key_hash=r["key_hash"], key_name=r["key_name"],
            allowed_agents=r["allowed_agents"], created_at=r["created_at"],
            last_used=r["last_used"],
        ) for r in rows]
