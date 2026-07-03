# -*- coding: utf-8 -*-
"""入口：多账号 iLink bot + 自助开通 API，同一进程。

- 启动时拉起 data/accounts 里所有已登录账号的收消息线程；
- 同时跑 FastAPI（默认 :8080），供 Vercel 前端自助开通新账号（热起线程）。

用法：
    python -m app.main                 # 起所有账号 + 开通 API
    python -m app.main --login         # 先 CLI 扫码加一个账号，再照常启动
    python -m app.main --login --qr-web # CLI 扫码用网页显示二维码
"""
from __future__ import annotations

import logging
import sys

from app import config, manager
from app.channel import ilink

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("weixin-agent")


def main() -> None:
    if "--login" in sys.argv:
        ilink.login(qr_web="--qr-web" in sys.argv)  # 存进 data/accounts/

    # 系统 Agent 注册（老师、Claude 等——没有 iLink bot 但可以被 call_agent 找到）
    try:
        from app.agent.teacher import register_in_bot_users as _reg_teacher
        _reg_teacher()
        print("👨‍🏫 老师 Agent 已注册（可用 call_agent(target_name='老师', ...) 找到）")
    except Exception as e:  # noqa: BLE001
        print(f"⚠️ 老师注册失败: {e}")
    try:
        from app.agent.claude_agent import register_in_bot_users as _reg_claude
        _reg_claude()
        print("🤖 Claude Agent 已注册（等 Mac daemon 连 /a2a/agents/claude/register 后自动上线）")
    except Exception as e:  # noqa: BLE001
        print(f"⚠️ Claude 注册失败: {e}")

    n = manager.start_all()
    print(f"🚀 已拉起 {n} 个已登录账号的收消息线程")
    for aid in manager.list_running():
        print(f"   • {aid}")

    # 打印当前 bot 用户注册状况
    from app.core.memory.bot_users import BotUsersStore
    users = BotUsersStore().list_all()
    running = [u for u in users if u.status == "running"]
    print(f"👥 bot 用户注册表：共 {len(users)} 人，running {len(running)}")
    for u in running:
        print(f"   • {u.display_name or '(未设名)'} / Agent={u.agent_name or '(未设)'} <{u.user_id}>")

    import uvicorn  # 延迟导入

    from app.api import app
    print(f"🌐 自助开通 API 监听 :{config.API_PORT}（POST /connect/start, GET /connect/status）")
    uvicorn.run(app, host="0.0.0.0", port=config.API_PORT, log_level="warning")


if __name__ == "__main__":
    main()
