# -*- coding: utf-8 -*-
"""实时旁观面板：把 runner 的事件 fanout 到 Pusher private channel。

事件类型（自定义，前端按 event 名分色显示）：
- user_message      用户在微信发的一句话（走 STT 后的文本）
- verbose_thinking  Agent 调工具前的思考（💭 前缀）
- verbose_tool_call 工具调用（🔧 前缀）
- verbose_tool_out  工具结果（↩️ 前缀）
- assistant_reply   最终回复给用户的话
- system_event      账号上下线之类

设计目标：
- 无 Pusher 配置时降级为 no-op，主流程不受影响
- publish 永不抛异常（远端故障不影响 bot 服务）
- 每个 user_id 有独立 private channel（private-user-{user_id}），签名鉴权
"""
from __future__ import annotations

import logging

from app import config

logger = logging.getLogger(__name__)

_client = None
_client_init_tried = False


def _get_client():
    global _client, _client_init_tried
    if _client is not None or _client_init_tried:
        return _client
    _client_init_tried = True
    if not config.pusher_ready():
        logger.info("Pusher 未配置，实时面板功能关闭")
        return None
    try:
        import pusher  # type: ignore
    except ImportError:
        logger.warning("pusher SDK 未安装，实时面板功能关闭")
        return None
    try:
        _client = pusher.Pusher(
            app_id=config.PUSHER_APP_ID,
            key=config.PUSHER_KEY,
            secret=config.PUSHER_SECRET,
            cluster=config.PUSHER_CLUSTER,
            ssl=True,
        )
        logger.info("Pusher 客户端已初始化（cluster=%s）", config.PUSHER_CLUSTER)
    except Exception as e:  # noqa: BLE001
        logger.warning("Pusher 初始化失败: %s", e)
        _client = None
    return _client


def channel_name(user_id: str) -> str:
    """Private channel 名。含 private- 前缀触发 Pusher 订阅签名。"""
    # user_id 里可能含 @，Pusher 不允许某些字符；简单替换
    safe = user_id.replace("@", "-at-").replace(".", "-")
    return f"private-user-{safe}"


def publish(user_id: str, event: str, data: dict) -> None:
    """Publish 到该用户的 private channel。远端失败静默降级。"""
    client = _get_client()
    if client is None or not user_id:
        return
    try:
        client.trigger(channel_name(user_id), event, data)
    except Exception as e:  # noqa: BLE001
        logger.warning("Pusher publish 失败 (%s/%s): %s", user_id, event, e)


def auth_subscribe(user_id: str, socket_id: str, channel: str) -> dict | None:
    """签发 private channel 订阅签名。只允许订阅自己的 channel。

    浏览器 subscribe private-* 前会 POST 到 /pusher/auth 端点带 socket_id + channel_name；
    server 拿本函数签名，返回给浏览器，浏览器再拿签名去 Pusher 服务器订阅。
    """
    client = _get_client()
    if client is None:
        return None
    expected = channel_name(user_id)
    if channel != expected:
        logger.warning("Pusher auth 拒绝：user %s 请求订阅 %s（应为 %s）",
                       user_id, channel, expected)
        return None
    try:
        return client.authenticate(channel=channel, socket_id=socket_id)
    except Exception as e:  # noqa: BLE001
        logger.warning("Pusher authenticate 失败: %s", e)
        return None
