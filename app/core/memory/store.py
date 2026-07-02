# -*- coding: utf-8 -*-
"""SQLite 存储层：替代 autoweixin 里 Redis(短期) + Milvus(长期) 的重型后端。

只负责"开库 + 建表 + 给连接"，记忆的算法逻辑在 short_term / long_term 里。
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

from app import config

_SCHEMA = """
CREATE TABLE IF NOT EXISTS stm_messages (
    session_id TEXT NOT NULL,
    seq        INTEGER NOT NULL,
    role       TEXT NOT NULL,
    content    TEXT NOT NULL,
    metadata   TEXT NOT NULL DEFAULT '{}',
    PRIMARY KEY (session_id, seq)
);

CREATE TABLE IF NOT EXISTS ltm_items (
    id         TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    content    TEXT NOT NULL,
    metadata   TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_ltm_session ON ltm_items(session_id);

-- 联系人：任务完成人/收件人名单。aliases 为逗号分隔的别名（昵称/简称）。
-- owner = bot 账号 account_id，多租户隔离：每个账号只看自己的联系人。
CREATE TABLE IF NOT EXISTS contacts (
    id              TEXT PRIMARY KEY,
    owner           TEXT NOT NULL DEFAULT '',
    name            TEXT NOT NULL,
    aliases         TEXT NOT NULL DEFAULT '',
    email           TEXT NOT NULL DEFAULT '',
    wechat_user_id  TEXT NOT NULL DEFAULT '',
    metadata        TEXT NOT NULL DEFAULT '{}',
    created_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_contacts_owner ON contacts(owner);

-- 结构化任务：拆解后的每条任务一行。
CREATE TABLE IF NOT EXISTS tasks (
    id            TEXT PRIMARY KEY,
    session_id    TEXT NOT NULL,
    assignee_name TEXT NOT NULL DEFAULT '',   -- 话里提到的完成人原文
    contact_id    TEXT,                       -- 匹配到的联系人 id（未匹配为 NULL）
    content       TEXT NOT NULL,
    start_time    TEXT NOT NULL DEFAULT '',
    end_time      TEXT NOT NULL DEFAULT '',
    assignee_email TEXT NOT NULL DEFAULT '',  -- 解析到的收件邮箱（个人联系人或公司通讯录）
    status        TEXT NOT NULL DEFAULT 'pending',
    created_at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_tasks_session ON tasks(session_id);

-- 全局员工通讯录：从 Google Drive 指定文件夹的表同步而来，所有用户共用。
CREATE TABLE IF NOT EXISTS employee_directory (
    id         TEXT PRIMARY KEY,
    name       TEXT NOT NULL,
    email      TEXT NOT NULL,
    aliases    TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL DEFAULT ''
);

-- 多条录制会话状态：按 user_id 隔离。active=1 时进入"只累积不总结"模式，
-- 收到结束语("就这些")才把 buffer 整段总结。
CREATE TABLE IF NOT EXISTS recording_state (
    user_id    TEXT PRIMARY KEY,
    active     INTEGER NOT NULL DEFAULT 0,
    buffer     TEXT NOT NULL DEFAULT '',
    count      INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL DEFAULT ''
);

-- 每用户设置：目前存各自的发件邮箱(SMTP)，按 user_id(=account_id) 隔离。
-- 不再共用一个发件箱：每个用户的任务邮件从 ta 自己的邮箱发出。
CREATE TABLE IF NOT EXISTS user_settings (
    user_id        TEXT PRIMARY KEY,
    smtp_host      TEXT NOT NULL DEFAULT '',
    smtp_port      INTEGER NOT NULL DEFAULT 465,
    smtp_user      TEXT NOT NULL DEFAULT '',
    smtp_pass      TEXT NOT NULL DEFAULT '',
    smtp_from_name TEXT NOT NULL DEFAULT '',
    updated_at     TEXT NOT NULL DEFAULT ''
);

-- 每用户偏好：verbose=1 时把思考/工具调用/结果都推到微信（详细模式）。
CREATE TABLE IF NOT EXISTS user_prefs (
    user_id    TEXT PRIMARY KEY,
    verbose    INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL DEFAULT ''
);

-- 全局 bot 用户注册表：所有扫码绑定的用户，主键 = from_user_id（微信号）。
-- display_name 是用户在别人 Agent 眼里的名字；agent_name 是自己给 Agent 起的名字。
-- status = 'running' 表示对应 bot 账号线程还在收消息；'offline' = 该用户当前不可达。
-- 用于"帮我问危博" → 查表定位到危博的 user_id → 触发跨 Agent 对话。
CREATE TABLE IF NOT EXISTS bot_users (
    user_id       TEXT PRIMARY KEY,
    display_name  TEXT NOT NULL DEFAULT '',
    agent_name    TEXT NOT NULL DEFAULT '',
    account_id    TEXT NOT NULL DEFAULT '',
    status        TEXT NOT NULL DEFAULT 'offline',
    last_seen     TEXT NOT NULL DEFAULT '',
    created_at    TEXT NOT NULL DEFAULT '',
    updated_at    TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_bot_users_display_name ON bot_users(display_name);
CREATE INDEX IF NOT EXISTS idx_bot_users_agent_name ON bot_users(agent_name);
"""


import threading

_init_lock = threading.Lock()
_initialized_paths: set[str] = set()


def _add_column_if_missing(conn, table: str, coldef: str) -> None:
    """给已存在的旧表补列（CREATE TABLE IF NOT EXISTS 不会改已存在的表）。"""
    col = coldef.split()[0]
    cols = [r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()]
    if cols and col not in cols:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {coldef}")


def _init_db(path: Path) -> None:
    """一次性：开 WAL + 建表/索引 + 轻量迁移。只在进程内每个库做一次。"""
    conn = sqlite3.connect(str(path), timeout=30)
    try:
        conn.execute("PRAGMA journal_mode=WAL")     # 持久化在库文件上
        conn.execute("PRAGMA synchronous=NORMAL")   # WAL 下安全且更快
        conn.executescript(_SCHEMA)
        # 迁移：给旧库补新增列
        _add_column_if_missing(conn, "tasks", "assignee_email TEXT NOT NULL DEFAULT ''")
        conn.commit()
    finally:
        conn.close()


def get_conn(db_path: Path | None = None) -> sqlite3.Connection:
    """打开 SQLite 连接（首次会初始化 WAL + 建表）。

    并发：多账号线程会同时写同一个库。SQLite 单写多读：
    - WAL 模式：读不被写阻塞，写之间排队（只初始化一次，避免每次 DDL 抢锁）。
    - busy_timeout=30s：拿不到写锁时等待而不是立刻报 "database is locked"。
    每个操作开/用/关一个连接、连接不跨线程共享、事务都很短，配合上面这些足够撑住几人到中等并发。
    """
    path = Path(db_path or config.DB_PATH)
    key = str(path)
    if key not in _initialized_paths:
        with _init_lock:
            if key not in _initialized_paths:
                path.parent.mkdir(parents=True, exist_ok=True)
                _init_db(path)
                _initialized_paths.add(key)
    conn = sqlite3.connect(str(path), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=30000")
    return conn
