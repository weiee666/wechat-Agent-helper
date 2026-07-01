# -*- coding: utf-8 -*-
"""统一配置：所有路径、环境变量、协议常量集中一处。

参照 autoweixin 的 app/config.py，但裁剪到本项目需要的范围。
"""
from __future__ import annotations

import os
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
except ImportError:
    pass

# ── 项目路径 ────────────────────────────────────────────────
BASE_DIR = Path(__file__).resolve().parent.parent     # 项目根 weixin-agent/
DATA_DIR = BASE_DIR / "data"                           # sqlite / 运行态数据
PROMPT_DIR = BASE_DIR / "prompts"                      # 可编辑提示词
DATA_DIR.mkdir(exist_ok=True)

DB_PATH = DATA_DIR / "memory.db"                       # 记忆库（短期+长期）
ILINK_TOKEN_FILE = DATA_DIR / "ilink_token.json"       # iLink 登录态

SUMMARIZE_PROMPT_FILE = PROMPT_DIR / "summarize.txt"

# ── LLM：DeepSeek（OpenAI 兼容，经 LangChain）────────────────
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
DEEPSEEK_BASE_URL = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
DEEPSEEK_MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-chat")

# ── 短期记忆窗口 / 压缩阈值 ──────────────────────────────────
STM_WINDOW_SIZE = int(os.getenv("STM_WINDOW", "20"))   # 超过这么多条触发压缩
STM_MAX_TOKENS = int(os.getenv("STM_MAX_TOKENS", "4000"))  # 或总 token 超阈值

# ── 邮件发送（对外交付 = 邮件）────────────────────────────────
# 发件邮箱按用户隔离，存在 user_settings 表里（见 core.memory.settings）。
# 不再有全局共享发件箱——每个用户用 set_sender_email 设置自己的。

# ── iLink 协议常量 ──────────────────────────────────────────
ILINK_BASE_URL = "https://ilinkai.weixin.qq.com"
ILINK_BOT_TYPE = "3"
ILINK_CHANNEL_VERSION = "1.0.2"
ILINK_QR_WEB_PORT = int(os.getenv("ILINK_QR_WEB_PORT", "8765"))  # --qr-web 登录时网页端口

# ── 自助开通门户 API ────────────────────────────────────────
API_PORT = int(os.getenv("API_PORT", "8080"))

# ── Google Drive 员工通讯录同步 ─────────────────────────────
GDRIVE_KEY_FILE = os.getenv("GDRIVE_KEY_FILE", str(BASE_DIR / "secrets" / "gdrive-key.json"))
GDRIVE_FOLDER_ID = os.getenv("GDRIVE_FOLDER_ID", "")          # 指定的共享文件夹/硬盘 ID
GDRIVE_SYNC_MINUTES = int(os.getenv("GDRIVE_SYNC_MINUTES", "30"))  # 定时同步间隔（仅能连Google的机器用）
# 推送通讯录到服务器的共享令牌（服务器在国内连不上Google，改由能连的机器拉表后推送）
DIRECTORY_SYNC_TOKEN = os.getenv("DIRECTORY_SYNC_TOKEN", "")


def gdrive_ready() -> bool:
    from pathlib import Path as _P
    return bool(GDRIVE_FOLDER_ID and _P(GDRIVE_KEY_FILE).exists())


# ── 实时旁观面板（Pusher）──────────────────────────────────
PUSHER_APP_ID = os.getenv("PUSHER_APP_ID", "").strip()
PUSHER_KEY = os.getenv("PUSHER_KEY", "").strip()
PUSHER_SECRET = os.getenv("PUSHER_SECRET", "").strip()
PUSHER_CLUSTER = os.getenv("PUSHER_CLUSTER", "").strip()
# 面板前端页面 URL（Vercel 部署后填）；带 ?token=xxx&user_id=yyy 分发给用户
DASHBOARD_URL_BASE = os.getenv("DASHBOARD_URL_BASE", "").strip()


def pusher_ready() -> bool:
    return bool(PUSHER_APP_ID and PUSHER_KEY and PUSHER_SECRET and PUSHER_CLUSTER)
