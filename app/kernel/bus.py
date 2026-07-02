# -*- coding: utf-8 -*-
"""通信总线（Kernel Bus）：所有跨用户的消息都必须经过这里。

Agent 不能直接调 iLink send_message；只能通过 bus 的 4 个函数：

1. deliver_handshake(A → B, message)
     A 主动找 B 时的系统握手信号，B 微信收到"🔔 有 Agent 交流｜A 的助手对你说 xxx"。
     **无需授权**——A 的一次发起本身就等于授权。

2. request_authorization(agent=B, target=A, proposed_text)
     B Agent 想把 proposed_text 发给 A。系统在 B 微信里弹"我想给 A 发送 xxx，回复允许/不允许"，
     等 B 用户回复。**B Agent 想给任何人发消息都必须走这一步**。

3. resolve_authorization(user_id, user_reply)
     用户在自己微信里回复"允许/不允许"时调用（handle_text 入口 hook 它）。
     - 允许 → 颁发令牌 → 立即调 deliver_with_token 发消息 → 系统通知 sender 已送达
     - 不允许 → 系统给 target 发一条"B 未应答"通知
     - 无 pending 或识别失败 → 返回 None，交回给 LLM 正常处理

4. deliver_with_token(token)
     用令牌把 pending 里存的 text 发到 target 微信。令牌一次性；用完即销毁。

5. deliver_system_notice(user_id, text)
     系统级消息（如"B 未应答"），不来自任何 Agent，无授权要求，靠 ctx 直接推。
"""
from __future__ import annotations

import logging

from app.channel import dispatch
from app.core.memory.bot_users import BotUsersStore
from app.kernel import auth

logger = logging.getLogger(__name__)


# ── 1. handshake ────────────────────────────────────────────
def deliver_handshake(from_user_id: str, from_display: str,
                      to_user_id: str, message: str) -> bool:
    """A 找 B 时的系统握手信号。B 微信收到"🔔 有 Agent 交流｜A 的助手对你说 xxx"。
    返回 True = 送达；False = ctx 无或 iLink 失败（sender 会看到"无法送达"）。"""
    if not to_user_id or not message:
        return False
    envelope = (
        f"🔔 有 Agent 交流\n"
        f"「{from_display}的助手」对你说：\n{message}\n\n"
        f"（如需回复，你的助手会先请示你）"
    )
    return dispatch.push_to_user(to_user_id, envelope)


# ── 2. request_authorization ────────────────────────────────
def request_authorization(agent_user_id: str, agent_display: str,
                          target_user_id: str, target_display: str,
                          proposed_text: str, conv_round: int = 0) -> bool:
    """B Agent 请求 B 用户授权：向 A 发送 proposed_text。
    B 微信弹一条'我想给 A 发送 xxx，回复允许/不允许'。
    返回 True = 请求已递交；False = 无法递交（B ctx 无 → 授权流程死路 → sender 端 fallback）。"""
    if not agent_user_id or not proposed_text:
        return False
    auth.create_pending(
        agent_user_id=agent_user_id,
        agent_display=agent_display,
        target_user_id=target_user_id,
        target_display=target_display,
        proposed_text=proposed_text,
        conv_round=conv_round,
    )
    prompt = (
        f"📝 授权请求（跨 Agent 对话第 {conv_round} 轮）\n"
        f"你的助手想给「{target_display}」发送：\n"
        f"—————\n{proposed_text}\n—————\n\n"
        f"回复「允许」/「不允许」（5 分钟内有效）"
    )
    ok = dispatch.push_to_user(agent_user_id, prompt)
    if not ok:
        # B 没有 ctx，授权流程走不下去——直接清掉 pending 让上游 fallback
        auth.clear_pending(agent_user_id)
    return ok


# ── 3. resolve_authorization ────────────────────────────────
def resolve_authorization(user_id: str, user_reply: str) -> str | None:
    """检查 user_id 是否有 pending 授权，尝试从 user_reply 分类。

    返回值：
    - None：没 pending 或识别为 unclear → 交回给 LLM 正常处理
    - "allowed"：授权已通过 + 消息已送达 target，poller 应回给 user 一个短确认
    - "denied"：已拒绝 + 已通知 target，poller 应回给 user 一个短确认
    - "unclear": 有 pending 但没听懂，poller 应让 bot 说"请回复允许或不允许"
    """
    p = auth.get_pending(user_id)
    if p is None:
        return None
    verdict = auth.classify_reply(user_reply)
    if verdict == "unclear":
        return "unclear"
    # 无论 allow/deny 都消耗 pending
    auth.clear_pending(user_id)
    if verdict == "allow":
        # 颁发令牌并立即消费
        token = auth.issue_token(p.target_user_id, p.proposed_text)
        _deliver_authorized_message(token, p)
        return "allowed"
    # denied
    notice = (
        f"❌ 「{p.agent_display}」的助手准备回复你，但被 {p.agent_display} 拒绝了发送。"
    )
    deliver_system_notice(p.target_user_id, notice)
    return "denied"


# ── 4. deliver_with_token ───────────────────────────────────
def _deliver_authorized_message(token: str, p) -> bool:
    """从 pending 拿到令牌+消息，把 B Agent 的原话发给 A。
    格式：'B 的助手 (Agent名)：<原话>'。"""
    entry = auth.spend_token(token)
    if entry is None:
        logger.warning("令牌无效或已过期 (pending id=%s)", p.id)
        return False
    target_uid, text = entry
    # 从 bot_users 拿 B 的 Agent 名字用来签名
    b_user = BotUsersStore().get(p.agent_user_id)
    agent_signature = f"{p.agent_display}的助手"
    if b_user and b_user.agent_name:
        agent_signature += f" {b_user.agent_name}"
    envelope = f"{agent_signature}：\n{text}"
    return dispatch.push_to_user(target_uid, envelope)


# ── 5. deliver_system_notice ────────────────────────────────
def deliver_system_notice(user_id: str, text: str) -> bool:
    """系统层通知（不来自任何 Agent）。用于"B 未应答"这种客观事实。"""
    if not user_id or not text:
        return False
    return dispatch.push_to_user(user_id, text)
