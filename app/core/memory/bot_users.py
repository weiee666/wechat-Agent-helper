# -*- coding: utf-8 -*-
"""bot 用户注册表：所有扫码绑定的 user_id（=from_user_id 微信号）+ 显示名 + Agent 名 + 运行状态。

用途：
- 允许一个用户的 Agent 定位到另一个用户的 Agent（"帮我问危博" → 查表拿 user_id）
- 记录每个用户的 bot 是不是正在收消息（status='running'/'offline'），避免向 offline 用户发消息
- 让用户自定义"我叫什么"、"我的 Agent 叫什么"，Agent 间对话时能自称

主键选择 user_id（wechat_id）而不是 account_id：user_id 稳定；account_id 每次重扫会变。
"""
from __future__ import annotations

import time
from dataclasses import dataclass

from app.core.memory.store import get_conn


@dataclass
class BotUser:
    user_id: str
    display_name: str
    agent_name: str
    account_id: str
    status: str  # 'running' | 'offline'
    last_seen: str
    last_ctx: str
    created_at: str
    updated_at: str


def _now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def _row_to_user(row) -> BotUser:
    # 兼容旧库：last_ctx 字段可能不存在（会由 _add_column_if_missing 迁移补齐）
    try:
        last_ctx = row["last_ctx"] or ""
    except (IndexError, KeyError):
        last_ctx = ""
    return BotUser(
        user_id=row["user_id"],
        display_name=row["display_name"],
        agent_name=row["agent_name"],
        account_id=row["account_id"],
        status=row["status"],
        last_seen=row["last_seen"],
        last_ctx=last_ctx,
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


class BotUsersStore:
    # ── 状态维护（manager 调用）──────────────────────────────
    def upsert_running(self, user_id: str, account_id: str) -> None:
        """账号线程启动时调用。保留已存在的 display_name / agent_name。"""
        if not user_id:
            return
        now = _now()
        conn = get_conn()
        try:
            existing = conn.execute(
                "SELECT user_id FROM bot_users WHERE user_id=?", (user_id,)
            ).fetchone()
            if existing is None:
                conn.execute(
                    "INSERT INTO bot_users(user_id, account_id, status, last_seen, created_at, updated_at)"
                    " VALUES (?,?,?,?,?,?)",
                    (user_id, account_id, "running", now, now, now),
                )
            else:
                conn.execute(
                    "UPDATE bot_users SET account_id=?, status='running', last_seen=?, updated_at=?"
                    " WHERE user_id=?",
                    (account_id, now, now, user_id),
                )
            conn.commit()
        finally:
            conn.close()

    def mark_offline(self, user_id: str) -> None:
        if not user_id:
            return
        conn = get_conn()
        try:
            conn.execute(
                "UPDATE bot_users SET status='offline', updated_at=? WHERE user_id=?",
                (_now(), user_id),
            )
            conn.commit()
        finally:
            conn.close()

    def mark_all_offline(self) -> None:
        """进程启动初始清零（后续 upsert_running 会把还在的标回来）。"""
        conn = get_conn()
        try:
            conn.execute("UPDATE bot_users SET status='offline', updated_at=?", (_now(),))
            conn.commit()
        finally:
            conn.close()

    def register_system_agent(self, user_id: str, display_name: str, agent_name: str) -> None:
        """注册一个"系统 Agent"（没有对应真实用户/iLink bot）。
        典型用途：老师（system:teacher）等跨用户共享 Agent。
        这些 Agent 会跟普通用户一样出现在 bot_users 表里，被 call_agent 用名字找到，
        但没有 iLink bot 也没有 pusher channel。
        status 强制 'running'（系统 Agent 永远在线）。"""
        if not user_id:
            return
        conn = get_conn()
        try:
            existing = conn.execute(
                "SELECT user_id FROM bot_users WHERE user_id=?", (user_id,)
            ).fetchone()
            now = _now()
            if existing is None:
                conn.execute(
                    "INSERT INTO bot_users(user_id, display_name, agent_name, account_id,"
                    " status, created_at, updated_at) VALUES (?,?,?,?,?,?,?)",
                    (user_id, display_name, agent_name, "system", "running", now, now),
                )
            else:
                conn.execute(
                    "UPDATE bot_users SET display_name=?, agent_name=?, status='running',"
                    " updated_at=? WHERE user_id=?",
                    (display_name, agent_name, now, user_id),
                )
            conn.commit()
        finally:
            conn.close()

    def touch_last_seen(self, user_id: str) -> None:
        """收到该用户消息时更新 last_seen。"""
        if not user_id:
            return
        conn = get_conn()
        try:
            conn.execute(
                "UPDATE bot_users SET last_seen=? WHERE user_id=?",
                (_now(), user_id),
            )
            conn.commit()
        finally:
            conn.close()

    def set_last_ctx(self, user_id: str, ctx: str) -> None:
        """收到该用户消息时保存 iLink context_token，用于以后主动 push 消息给他。"""
        if not user_id or not ctx:
            return
        conn = get_conn()
        try:
            conn.execute(
                "UPDATE bot_users SET last_ctx=?, last_seen=?, updated_at=? WHERE user_id=?",
                (ctx, _now(), _now(), user_id),
            )
            conn.commit()
        finally:
            conn.close()

    # ── 用户主动填写姓名 / Agent 名 ──────────────────────────
    def set_display_name(self, user_id: str, name: str) -> None:
        conn = get_conn()
        try:
            # 保证行存在（可能用户还没被 manager 记录）
            row = conn.execute("SELECT user_id FROM bot_users WHERE user_id=?", (user_id,)).fetchone()
            now = _now()
            if row is None:
                conn.execute(
                    "INSERT INTO bot_users(user_id, display_name, created_at, updated_at) VALUES (?,?,?,?)",
                    (user_id, name.strip(), now, now),
                )
            else:
                conn.execute(
                    "UPDATE bot_users SET display_name=?, updated_at=? WHERE user_id=?",
                    (name.strip(), now, user_id),
                )
            conn.commit()
        finally:
            conn.close()

    def set_agent_name(self, user_id: str, name: str) -> None:
        conn = get_conn()
        try:
            row = conn.execute("SELECT user_id FROM bot_users WHERE user_id=?", (user_id,)).fetchone()
            now = _now()
            if row is None:
                conn.execute(
                    "INSERT INTO bot_users(user_id, agent_name, created_at, updated_at) VALUES (?,?,?,?)",
                    (user_id, name.strip(), now, now),
                )
            else:
                conn.execute(
                    "UPDATE bot_users SET agent_name=?, updated_at=? WHERE user_id=?",
                    (name.strip(), now, user_id),
                )
            conn.commit()
        finally:
            conn.close()

    # ── 查询 ────────────────────────────────────────────────
    def get(self, user_id: str) -> BotUser | None:
        conn = get_conn()
        try:
            row = conn.execute(
                "SELECT * FROM bot_users WHERE user_id=?", (user_id,)
            ).fetchone()
            return _row_to_user(row) if row else None
        finally:
            conn.close()

    def find_by_name(self, name: str) -> BotUser | None:
        """按 display_name 或 agent_name 精确匹配（去空白，不区分大小写）。
        返回第一个 running 的；没 running 就返回第一个 offline 的。"""
        if not name or not name.strip():
            return None
        needle = name.strip().lower()
        conn = get_conn()
        try:
            rows = conn.execute(
                "SELECT * FROM bot_users WHERE "
                "LOWER(TRIM(display_name)) = ? OR LOWER(TRIM(agent_name)) = ?",
                (needle, needle),
            ).fetchall()
        finally:
            conn.close()
        if not rows:
            return None
        # 优先 running
        for r in rows:
            if r["status"] == "running":
                return _row_to_user(r)
        return _row_to_user(rows[0])

    def list_all(self) -> list[BotUser]:
        conn = get_conn()
        try:
            rows = conn.execute(
                "SELECT * FROM bot_users ORDER BY status DESC, updated_at DESC"
            ).fetchall()
            return [_row_to_user(r) for r in rows]
        finally:
            conn.close()

    def list_running(self) -> list[BotUser]:
        conn = get_conn()
        try:
            rows = conn.execute(
                "SELECT * FROM bot_users WHERE status='running' ORDER BY updated_at DESC"
            ).fetchall()
            return [_row_to_user(r) for r in rows]
        finally:
            conn.close()
