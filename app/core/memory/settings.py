# -*- coding: utf-8 -*-
"""每用户偏好（按 user_id 隔离）：当前只有 verbose 开关。"""
from __future__ import annotations

from datetime import datetime

from app.core.memory.store import get_conn


class UserSettingsStore:
    def get_verbose(self, user_id: str) -> bool:
        """该用户是否开启详细模式（把思考/工具调用推到微信）。"""
        conn = get_conn()
        try:
            r = conn.execute("SELECT verbose FROM user_prefs WHERE user_id=?", (user_id,)).fetchone()
            return bool(r and r["verbose"])
        finally:
            conn.close()

    def set_verbose(self, user_id: str, on: bool) -> None:
        conn = get_conn()
        try:
            conn.execute(
                "INSERT INTO user_prefs(user_id, verbose, updated_at) VALUES (?,?,?)"
                " ON CONFLICT(user_id) DO UPDATE SET verbose=excluded.verbose, updated_at=excluded.updated_at",
                (user_id, 1 if on else 0, datetime.now().isoformat(timespec="seconds")),
            )
            conn.commit()
        finally:
            conn.close()

    def get_require_authorization(self, user_id: str) -> bool:
        """本用户是否要求"外部 Agent 向我微信 push 消息前必须请示我"。默认 False。"""
        conn = get_conn()
        try:
            r = conn.execute(
                "SELECT require_authorization FROM user_prefs WHERE user_id=?", (user_id,)
            ).fetchone()
            return bool(r and r["require_authorization"])
        finally:
            conn.close()

    def set_require_authorization(self, user_id: str, on: bool) -> None:
        conn = get_conn()
        try:
            conn.execute(
                "INSERT INTO user_prefs(user_id, require_authorization, updated_at) VALUES (?,?,?)"
                " ON CONFLICT(user_id) DO UPDATE SET require_authorization=excluded.require_authorization,"
                " updated_at=excluded.updated_at",
                (user_id, 1 if on else 0, datetime.now().isoformat(timespec="seconds")),
            )
            conn.commit()
        finally:
            conn.close()
