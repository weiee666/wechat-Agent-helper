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


# ── 实时旁观面板（Pusher）──────────────────────────────────
PUSHER_APP_ID = os.getenv("PUSHER_APP_ID", "").strip()
PUSHER_KEY = os.getenv("PUSHER_KEY", "").strip()
PUSHER_SECRET = os.getenv("PUSHER_SECRET", "").strip()
PUSHER_CLUSTER = os.getenv("PUSHER_CLUSTER", "").strip()
# 面板前端页面 URL（Vercel 部署后填）；带 ?token=xxx&user_id=yyy 分发给用户
DASHBOARD_URL_BASE = os.getenv("DASHBOARD_URL_BASE", "").strip()


def pusher_ready() -> bool:
    return bool(PUSHER_APP_ID and PUSHER_KEY and PUSHER_SECRET and PUSHER_CLUSTER)


# ── A2A（Agent-to-Agent 协议）─────────────────────────────
# 我们对外暴露的 Base URL。外部 Agent Card 里的 endpoint URL 用它拼。
# 生产要走 HTTPS + 域名；开发默认 IP+端口。空字符串时 Agent Card 不生成 URL。
A2A_BASE_URL = os.getenv("A2A_BASE_URL", "").strip()
# 服务方名字（放进 Agent Card 的 provider）
A2A_PROVIDER_NAME = os.getenv("A2A_PROVIDER_NAME", "weixin-agent").strip()
