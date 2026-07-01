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

    n = manager.start_all()
    print(f"🚀 已拉起 {n} 个已登录账号的收消息线程")
    for aid in manager.list_running():
        print(f"   • {aid}")

    # 公司通讯录：本服务器在国内连不上 Google，不自己拉表。
    # 改由能连 Google 的机器（Mac / Vercel）拉表后 POST /directory/sync 推送进来。
    from app.core.memory.directory import EmployeeDirectory
    print(f"📒 公司通讯录现有 {EmployeeDirectory().count()} 人（经外部推送更新）")

    import uvicorn  # 延迟导入

    from app.api import app
    print(f"🌐 自助开通 API 监听 :{config.API_PORT}（POST /connect/start, GET /connect/status）")
    uvicorn.run(app, host="0.0.0.0", port=config.API_PORT, log_level="warning")


if __name__ == "__main__":
    main()
