# -*- coding: utf-8 -*-
"""多条录制会话状态（按 user_id 隔离）。

开始录制后，用户连发的每条消息都累积进 buffer、不触发总结；
直到收到结束语才把整段交给上层总结。
"""
from __future__ import annotations

from datetime import datetime

from app.core.memory.store import get_conn


class RecordingStore:
    def is_active(self, user_id: str) -> bool:
        conn = get_conn()
        try:
            r = conn.execute("SELECT active FROM recording_state WHERE user_id=?", (user_id,)).fetchone()
            return bool(r and r["active"])
        finally:
            conn.close()

    def start(self, user_id: str) -> None:
        conn = get_conn()
        try:
            conn.execute(
                "INSERT INTO recording_state(user_id, active, buffer, count, updated_at)"
                " VALUES (?,1,'',0,?)"
                " ON CONFLICT(user_id) DO UPDATE SET active=1, buffer='', count=0, updated_at=excluded.updated_at",
                (user_id, datetime.now().isoformat(timespec="seconds")),
            )
            conn.commit()
        finally:
            conn.close()

    def append(self, user_id: str, text: str) -> int:
        """累积一条，返回当前累积条数。"""
        conn = get_conn()
        try:
            r = conn.execute("SELECT buffer, count FROM recording_state WHERE user_id=?", (user_id,)).fetchone()
            buffer = (r["buffer"] if r else "") or ""
            count = (r["count"] if r else 0) or 0
            buffer = f"{buffer}\n{text}" if buffer else text
            count += 1
            conn.execute(
                "UPDATE recording_state SET buffer=?, count=?, updated_at=? WHERE user_id=?",
                (buffer, count, datetime.now().isoformat(timespec="seconds"), user_id),
            )
            conn.commit()
            return count
        finally:
            conn.close()

    def finish(self, user_id: str) -> str:
        """结束录制：返回累积的整段文本并清空。"""
        conn = get_conn()
        try:
            r = conn.execute("SELECT buffer FROM recording_state WHERE user_id=?", (user_id,)).fetchone()
            buffer = (r["buffer"] if r else "") or ""
            conn.execute(
                "UPDATE recording_state SET active=0, buffer='', count=0, updated_at=? WHERE user_id=?",
                (datetime.now().isoformat(timespec="seconds"), user_id),
            )
            conn.commit()
            return buffer
        finally:
            conn.close()

    def cancel(self, user_id: str) -> None:
        conn = get_conn()
        try:
            conn.execute(
                "UPDATE recording_state SET active=0, buffer='', count=0, updated_at=? WHERE user_id=?",
                (datetime.now().isoformat(timespec="seconds"), user_id),
            )
            conn.commit()
        finally:
            conn.close()
