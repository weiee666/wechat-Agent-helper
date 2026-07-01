# -*- coding: utf-8 -*-
"""结构化任务存储（长期记忆的一部分）。"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime

from app.core.memory.store import get_conn


@dataclass
class Task:
    id: str
    session_id: str
    assignee_name: str
    contact_id: str | None
    content: str
    start_time: str
    end_time: str
    assignee_email: str = ""
    status: str = "pending"


class TaskStore:
    def add(self, session_id: str, assignee_name: str, content: str,
            start_time: str = "", end_time: str = "", contact_id: str | None = None,
            assignee_email: str = "") -> str:
        tid = uuid.uuid4().hex
        conn = get_conn()
        try:
            conn.execute(
                "INSERT INTO tasks(id, session_id, assignee_name, contact_id, content,"
                " start_time, end_time, assignee_email, status, created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (tid, session_id, assignee_name, contact_id, content,
                 start_time, end_time, assignee_email, "pending",
                 datetime.now().isoformat(timespec="seconds")),
            )
            conn.commit()
        finally:
            conn.close()
        return tid

    def mark_sent(self, task_ids: list[str]) -> None:
        if not task_ids:
            return
        conn = get_conn()
        try:
            conn.executemany("UPDATE tasks SET status='sent' WHERE id=?", [(i,) for i in task_ids])
            conn.commit()
        finally:
            conn.close()

    def list_by_session(self, session_id: str, status: str | None = None) -> list[Task]:
        conn = get_conn()
        try:
            if status:
                rows = conn.execute(
                    "SELECT * FROM tasks WHERE session_id=? AND status=? ORDER BY created_at",
                    (session_id, status),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM tasks WHERE session_id=? ORDER BY created_at", (session_id,)
                ).fetchall()
            return [
                Task(
                    id=r["id"], session_id=r["session_id"], assignee_name=r["assignee_name"],
                    contact_id=r["contact_id"], content=r["content"],
                    start_time=r["start_time"], end_time=r["end_time"],
                    assignee_email=r["assignee_email"] if "assignee_email" in r.keys() else "",
                    status=r["status"],
                )
                for r in rows
            ]
        finally:
            conn.close()
