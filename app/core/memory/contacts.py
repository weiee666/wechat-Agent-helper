# -*- coding: utf-8 -*-
"""联系人存储与精准匹配（长期记忆的一部分），按 owner(=bot账号) 多租户隔离。

匹配优先级：精确姓名 > 别名精确 > 姓名/别名包含。匹配不到返回 None，
交由上层（agent）发起澄清反问。所有读写都限定在某个 owner 名下。
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime

from app.core.memory.store import get_conn


class Contact:
    __slots__ = ("id", "name", "aliases", "email", "wechat_user_id", "metadata")

    def __init__(self, id, name, aliases, email, wechat_user_id, metadata):
        self.id = id
        self.name = name
        self.aliases = aliases          # list[str]
        self.email = email
        self.wechat_user_id = wechat_user_id
        self.metadata = metadata

    def __repr__(self) -> str:
        return f"Contact({self.name!r}, email={self.email!r})"


def _row_to_contact(r) -> Contact:
    return Contact(
        id=r["id"],
        name=r["name"],
        aliases=[a for a in (r["aliases"] or "").split(",") if a],
        email=r["email"],
        wechat_user_id=r["wechat_user_id"],
        metadata=json.loads(r["metadata"] or "{}"),
    )


class ContactStore:
    def add(self, owner: str, name: str, email: str = "", aliases: list[str] | None = None,
            wechat_user_id: str = "", metadata: dict | None = None) -> str:
        cid = uuid.uuid4().hex
        conn = get_conn()
        try:
            conn.execute(
                "INSERT INTO contacts(id, owner, name, aliases, email, wechat_user_id, metadata, created_at)"
                " VALUES (?,?,?,?,?,?,?,?)",
                (cid, owner, name, ",".join(aliases or []), email, wechat_user_id,
                 json.dumps(metadata or {}, ensure_ascii=False),
                 datetime.now().isoformat(timespec="seconds")),
            )
            conn.commit()
        finally:
            conn.close()
        return cid

    def add_or_update(self, owner: str, name: str, email: str = "",
                      aliases: list[str] | None = None, wechat_user_id: str = "") -> tuple[str, bool]:
        """在 owner 名下按精确姓名 upsert。返回 (id, 是否新增)。"""
        conn = get_conn()
        try:
            row = conn.execute(
                "SELECT * FROM contacts WHERE owner=? AND name=?", (owner, name)
            ).fetchone()
            if row:
                merged = sorted(set(
                    [a for a in (row["aliases"] or "").split(",") if a] + (aliases or [])
                ))
                conn.execute(
                    "UPDATE contacts SET aliases=?, email=COALESCE(NULLIF(?, ''), email),"
                    " wechat_user_id=COALESCE(NULLIF(?, ''), wechat_user_id) WHERE id=?",
                    (",".join(merged), email, wechat_user_id, row["id"]),
                )
                conn.commit()
                return row["id"], False
        finally:
            conn.close()
        return self.add(owner, name, email=email, aliases=aliases, wechat_user_id=wechat_user_id), True

    def list_all(self, owner: str) -> list[Contact]:
        conn = get_conn()
        try:
            rows = conn.execute(
                "SELECT * FROM contacts WHERE owner=? ORDER BY name", (owner,)
            ).fetchall()
            return [_row_to_contact(r) for r in rows]
        finally:
            conn.close()

    def match(self, owner: str, name: str) -> Contact | None:
        """在 owner 名下按姓名/别名精准匹配；歧义或无匹配返回 None。"""
        name = (name or "").strip()
        if not name:
            return None
        contacts = self.list_all(owner)

        exact = [c for c in contacts if c.name == name]
        if len(exact) == 1:
            return exact[0]
        alias_hit = [c for c in contacts if name in c.aliases]
        if len(alias_hit) == 1:
            return alias_hit[0]
        contains = [
            c for c in contacts
            if name in c.name or c.name in name or any(name in a or a in name for a in c.aliases)
        ]
        if len(contains) == 1:
            return contains[0]
        return None
