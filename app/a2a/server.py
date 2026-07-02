# -*- coding: utf-8 -*-
"""A2A HTTP server：把内部 Agent 暴露为符合 A2A 协议的 endpoint。

挂载路径：
    GET  /a2a/agents                        → 列出可访问的 Agent
    GET  /a2a/agents/{user_id}/agent-card   → 返回该 Agent 的 AgentCard（JSON）
    POST /a2a/agents/{user_id}/rpc          → JSON-RPC 收 A2A task

所有端点都需要 X-API-Key header 校验。
"""
from __future__ import annotations

from urllib.parse import unquote

from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from google.protobuf.json_format import MessageToDict

from a2a.server.agent_execution import SimpleRequestContextBuilder
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.routes.jsonrpc_dispatcher import JsonRpcDispatcher
from a2a.server.tasks import InMemoryTaskStore

from app.a2a.api_keys import ApiKeyStore
from app.a2a.cards import build_agent_card
from app.a2a.executor import WeixinAgentExecutor
from app.core.memory.bot_users import BotUsersStore


# 全局任务库（跨 request 共享，SDK 用它维护 task 的 status/history）
_TASK_STORE = InMemoryTaskStore()

router = APIRouter(prefix="/a2a")


def _require_api_key(x_api_key: str, requested_agent_uid: str):
    if not x_api_key:
        raise HTTPException(status_code=401,
                            detail="X-API-Key header required")
    meta = ApiKeyStore().verify(x_api_key, requested_agent_uid)
    if meta is None:
        raise HTTPException(status_code=403,
                            detail="Invalid or unauthorized API key for this agent")
    return meta


@router.get("/agents/{user_id:path}/agent-card")
def get_agent_card(user_id: str, x_api_key: str = Header(default="")):
    """返回指定用户 Agent 的 A2A AgentCard（JSON 形态）。

    user_id 因含 @/. 走 :path 匹配。请求方需带 X-API-Key。
    """
    uid = unquote(user_id)
    _require_api_key(x_api_key, uid)

    user = BotUsersStore().get(uid)
    if user is None:
        raise HTTPException(status_code=404, detail=f"No agent for user {uid}")
    if user.status != "running":
        raise HTTPException(status_code=503, detail=f"Agent for {uid} is offline")

    card = build_agent_card(user)
    return JSONResponse(MessageToDict(card, preserving_proto_field_name=True))


@router.post("/agents/{user_id:path}/rpc")
async def rpc_endpoint(user_id: str, request: Request,
                       x_api_key: str = Header(default="")):
    """A2A JSON-RPC 端点：接收 message/send、tasks/get、tasks/cancel 等请求。
    Body 是 JSON-RPC 2.0 envelope，SDK 的 JsonRpcDispatcher 分发给 DefaultRequestHandler。"""
    uid = unquote(user_id)
    key_meta = _require_api_key(x_api_key, uid)

    user = BotUsersStore().get(uid)
    if user is None:
        raise HTTPException(status_code=404, detail=f"No agent for user {uid}")
    if user.status != "running":
        raise HTTPException(status_code=503, detail=f"Agent for {uid} is offline")

    # 用 API Key 名字作为 caller 显示名（B 微信里看到"[XX 的助手] ..."）
    caller_display = key_meta.key_name or "外部 Agent"
    executor = WeixinAgentExecutor(target_user_id=uid, caller_display=caller_display)
    card = build_agent_card(user)
    handler = DefaultRequestHandler(
        agent_executor=executor,
        task_store=_TASK_STORE,
        agent_card=card,
    )
    dispatcher = JsonRpcDispatcher(request_handler=handler)
    return await dispatcher.handle_requests(request)


@router.get("/agents")
def list_agents(x_api_key: str = Header(default="")):
    """列出所有 running 的 Agent（简洁列表，只有 user_id / display_name / agent_name）。
    需要能访问 * 或至少一个 agent 的 API Key。"""
    if not x_api_key:
        raise HTTPException(status_code=401, detail="X-API-Key header required")
    meta = ApiKeyStore().verify(x_api_key)
    if meta is None:
        raise HTTPException(status_code=403, detail="Invalid API key")
    running = [u for u in BotUsersStore().list_all() if u.status == "running"]
    # 若 key 有白名单，只列白名单内
    if meta.allowed_agents != "*":
        allow = {a.strip() for a in meta.allowed_agents.split(",") if a.strip()}
        running = [u for u in running if u.user_id in allow]
    return {
        "agents": [
            {
                "user_id": u.user_id,
                "display_name": u.display_name,
                "agent_name": u.agent_name,
                "agent_card_url": f"/a2a/agents/{u.user_id}/agent-card",
            }
            for u in running
        ],
    }
