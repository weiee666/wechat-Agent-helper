# -*- coding: utf-8 -*-
"""运行时账号管理器：在一个进程里动态起/停每个 bot 账号的收消息线程。

启动时拉起已有账号；自助开通确认后热加一个新账号（不打断其它账号）。
"""
from __future__ import annotations

import logging
import threading

from app.channel import accounts, poller
from app.core.memory.bot_users import BotUsersStore

logger = logging.getLogger("weixin-agent.manager")

# account_id -> (thread, stop_event, user_id)
_threads: dict[str, tuple[threading.Thread, threading.Event, str]] = {}
_lock = threading.Lock()


def add_account(session: dict) -> bool:
    """为某账号起一个收消息线程。同一个微信号(userId)若已有旧 bot，先停掉它并删其文件。
    返回是否新起。"""
    account_id = session.get("accountId") or "default"
    user_id = session.get("userId") or ""
    with _lock:
        # 同一用户重扫：停掉并删除其它 bot（保证一个微信号最多一个活 bot）
        if user_id:
            for old_aid, (t, stop, ouid) in list(_threads.items()):
                if old_aid != account_id and ouid == user_id:
                    stop.set()
                    _threads.pop(old_aid, None)
                    accounts.remove_account(old_aid)
                    logger.info("用户 %s 重扫，已停掉并删除旧 bot %s", user_id, old_aid)
        existing = _threads.get(account_id)
        if existing and existing[0].is_alive():
            return False
        stop = threading.Event()
        t = threading.Thread(target=poller.poll_account, args=(session, stop),
                             daemon=True, name=f"poll-{account_id}")
        t.start()
        _threads[account_id] = (t, stop, user_id)
    # 同步 bot_users 表：标记为 running（保留已有的 display_name / agent_name）
    if user_id:
        BotUsersStore().upsert_running(user_id=user_id, account_id=account_id)
    logger.info("已起账号收消息线程: %s (userId=%s)", account_id, user_id or "?")
    return True


def start_all() -> int:
    """拉起 data/accounts 里所有已登录账号。返回起了几个。"""
    # 启动时先把所有 bot_users 状态置为 offline；add_account 会把还活着的标回 running
    BotUsersStore().mark_all_offline()
    sessions = accounts.load_accounts()
    n = sum(1 for s in sessions if add_account(s))
    return n


def list_running() -> list[str]:
    with _lock:
        return [aid for aid, (t, _s, _u) in _threads.items() if t.is_alive()]


def stop_all() -> None:
    with _lock:
        for _t, stop, _u in _threads.values():
            stop.set()
    BotUsersStore().mark_all_offline()


def mark_user_offline(user_id: str) -> None:
    """poller 循环退出时调用（session timeout 等），把该用户标为 offline。"""
    if user_id:
        BotUsersStore().mark_offline(user_id)
