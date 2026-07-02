# -*- coding: utf-8 -*-
"""为每个内部 bot 用户生成 A2A AgentCard。

外部 Agent 通过 GET /agents/{user_id}/agent-card 拿到这份 Card，
从而知道：这个 Agent 叫什么、能做什么、endpoint URL、需要什么授权。

Agent Card 的独特扩展：
- Security scheme = X-API-Key（外部先拿 API Key 才能拉 Card 和发 Task）
- capabilities.push_notifications = False（我们不做 A2A push 通知外部；用 iLink 内部推）
- 附加 tag "input-required:target-user-approval" 提示外部：本 Agent 处理请求时
  可能进入 INPUT_REQUIRED 状态等待"target 用户"的授权（不是 client 用户），
  这对外部客户端是异步等待，不需要额外交互。
"""
from __future__ import annotations

from app import config
from a2a.types import (
    AgentCapabilities,
    AgentCard,
    AgentInterface,
    AgentProvider,
    AgentSkill,
    SecurityRequirement,
    SecurityScheme,
    APIKeySecurityScheme,
)
from app.core.memory.bot_users import BotUser


def _endpoint_url(user_id: str) -> str:
    if not config.A2A_BASE_URL:
        return ""
    base = config.A2A_BASE_URL.rstrip("/")
    # user_id 里含 @ 和 .，URL-encode
    from urllib.parse import quote
    return f"{base}/a2a/agents/{quote(user_id, safe='')}/rpc"


def build_agent_card(user: BotUser) -> AgentCard:
    """从 bot_users 记录构造 A2A AgentCard。"""
    card = AgentCard()
    # 显示名字：优先 agent_name，退到 display_name+的助手
    if user.agent_name:
        card.name = user.agent_name
    elif user.display_name:
        card.name = f"{user.display_name}的助手"
    else:
        card.name = "个人 AI 助手"

    display = user.display_name or "用户"
    card.description = (
        f"这是 {display} 的个人 AI 助手，通过微信为 {display} 服务。"
        f"支持记录任务、日常聊天、以及与其他 Agent 协作。"
        f"注意：所有向外部发出的消息都需要 {display} 本人授权（授权流程内部完成，"
        f"外部客户端只需按标准 A2A 协议发送请求即可，可能进入 INPUT_REQUIRED 状态）。"
    )
    card.version = "1.0.0"

    # 服务方
    provider = AgentProvider()
    provider.organization = config.A2A_PROVIDER_NAME
    card.provider.CopyFrom(provider)

    # Endpoint
    url = _endpoint_url(user.user_id)
    if url:
        iface = AgentInterface()
        iface.url = url
        iface.protocol_binding = "jsonrpc"
        iface.protocol_version = "1.0"
        card.supported_interfaces.append(iface)

    # 能力
    caps = AgentCapabilities()
    caps.streaming = False
    caps.push_notifications = False
    caps.extended_agent_card = False
    card.capabilities.CopyFrom(caps)

    # 输入 / 输出模式（只支持文本）
    card.default_input_modes.extend(["text/plain"])
    card.default_output_modes.extend(["text/plain"])

    # Security scheme：只接受 X-API-Key
    api_scheme = APIKeySecurityScheme()
    api_scheme.location = "header"
    api_scheme.name = "X-API-Key"
    sec_scheme = SecurityScheme()
    sec_scheme.api_key_security_scheme.CopyFrom(api_scheme)
    card.security_schemes["apiKey"].CopyFrom(sec_scheme)

    req = SecurityRequirement()
    # schemes 是 map<string, StringList>；空列表表示"用此 scheme 即可"
    req.schemes["apiKey"].list.extend([])
    card.security_requirements.append(req)

    # 通用聊天技能
    chat_skill = AgentSkill()
    chat_skill.id = "general-chat"
    chat_skill.name = f"与 {display} 交流"
    chat_skill.description = (
        f"向 {display} 转达你的消息。{display} 授权后本 Agent 会代 {display} 回复。"
        f"如果 {display} 拒绝或超时未响应，你会收到系统通知。"
    )
    chat_skill.tags.extend(["chat", "delegation", "input-required:target-user-approval"])
    chat_skill.examples.append("请转告我：明天的会议改到下午三点")
    chat_skill.examples.append("你好，想问一下这周有没有空聊一下？")
    chat_skill.input_modes.extend(["text/plain"])
    chat_skill.output_modes.extend(["text/plain"])
    card.skills.append(chat_skill)

    return card
