# -*- coding: utf-8 -*-
"""多账号（多租户）持久化：每个 bot 账号一份登录态，存 data/accounts/<id>.json。

替代原来的单文件 data/ilink_token.json。account_id 即各 bot 的隔离键。
"""
from __future__ import annotations

import json
import re

from app import config

ACCOUNTS_DIR = config.DATA_DIR / "accounts"


def _safe(account_id: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]", "_", account_id or "unknown")


def save_account(session: dict) -> None:
    ACCOUNTS_DIR.mkdir(parents=True, exist_ok=True)
    aid = session.get("accountId") or "unknown"
    path = ACCOUNTS_DIR / f"{_safe(aid)}.json"
    path.write_text(json.dumps(session, ensure_ascii=False, indent=2), encoding="utf-8")
    try:
        path.chmod(0o600)
    except OSError:
        pass


def load_accounts() -> list[dict]:
    out: list[dict] = []
    if ACCOUNTS_DIR.exists():
        for f in sorted(ACCOUNTS_DIR.glob("*.json")):
            try:
                out.append(json.loads(f.read_text(encoding="utf-8")))
            except (json.JSONDecodeError, OSError):
                continue
    # 迁移：若没有多账号文件但存在旧的单文件 token，导入它
    if not out and config.ILINK_TOKEN_FILE.exists():
        try:
            s = json.loads(config.ILINK_TOKEN_FILE.read_text(encoding="utf-8"))
            save_account(s)
            out.append(s)
        except (json.JSONDecodeError, OSError):
            pass
    return out


def remove_account(account_id: str) -> None:
    (ACCOUNTS_DIR / f"{_safe(account_id)}.json").unlink(missing_ok=True)
