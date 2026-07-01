# -*- coding: utf-8 -*-
"""每用户设置（按 user_id 隔离）。当前：各自的发件邮箱 SMTP。

⚠️ 安全：授权码/密码目前明文存 SQLite。库文件 0600、仅服务器本机可读；
后续可加密（见 README 待办）。
"""
from __future__ import annotations

from datetime import datetime

from app.core.memory.store import get_conn

# 常见邮箱服务商域名 → (SMTP 服务器, 端口)。自定义域名(如企业邮)需显式给 host。
SMTP_HOSTS: dict[str, tuple[str, int]] = {
    "gmail.com": ("smtp.gmail.com", 465),
    "qq.com": ("smtp.qq.com", 465),
    "foxmail.com": ("smtp.qq.com", 465),
    "163.com": ("smtp.163.com", 465),
    "126.com": ("smtp.126.com", 465),
    "outlook.com": ("smtp.office365.com", 587),
    "hotmail.com": ("smtp.office365.com", 587),
    "sina.com": ("smtp.sina.com", 465),
    "aliyun.com": ("smtp.aliyun.com", 465),
    # 公司域名（Google Workspace，走 Gmail 的 SMTP）
    "tenthglobal.org": ("smtp.gmail.com", 465),
}


def infer_smtp(email: str) -> tuple[str, int] | None:
    """按邮箱域名推断 SMTP 服务器；未知返回 None。"""
    domain = (email.rsplit("@", 1)[-1] if "@" in email else "").lower()
    return SMTP_HOSTS.get(domain)


class UserSettingsStore:
    def get_smtp(self, user_id: str) -> dict | None:
        """返回该用户的 SMTP 配置；未配置或不完整返回 None。"""
        conn = get_conn()
        try:
            r = conn.execute("SELECT * FROM user_settings WHERE user_id=?", (user_id,)).fetchone()
        finally:
            conn.close()
        if not r or not r["smtp_user"] or not r["smtp_pass"] or not r["smtp_host"]:
            return None
        return {
            "host": r["smtp_host"],
            "port": int(r["smtp_port"] or 465),
            "user": r["smtp_user"],
            "password": r["smtp_pass"],
            "from_name": r["smtp_from_name"] or r["smtp_user"],
        }

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

    def set_smtp(self, user_id: str, host: str, port: int, user: str,
                 password: str, from_name: str = "") -> None:
        conn = get_conn()
        try:
            conn.execute(
                "INSERT INTO user_settings(user_id, smtp_host, smtp_port, smtp_user, smtp_pass,"
                " smtp_from_name, updated_at) VALUES (?,?,?,?,?,?,?)"
                " ON CONFLICT(user_id) DO UPDATE SET smtp_host=excluded.smtp_host,"
                " smtp_port=excluded.smtp_port, smtp_user=excluded.smtp_user,"
                " smtp_pass=excluded.smtp_pass, smtp_from_name=excluded.smtp_from_name,"
                " updated_at=excluded.updated_at",
                (user_id, host, port, user, password, from_name,
                 datetime.now().isoformat(timespec="seconds")),
            )
            conn.commit()
        finally:
            conn.close()
