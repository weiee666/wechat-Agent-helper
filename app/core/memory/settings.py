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
