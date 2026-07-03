# -*- coding: utf-8 -*-
"""自助开通 + 旁观面板 HTTP API（FastAPI）。

- POST /connect/start        → 现生成一张新二维码，返回 {ticket, status, qr_svg}
- GET  /connect/status?ticket→ 该会话当前状态；confirmed 时账号已热起
- GET  /healthz              → 健康检查 + 在跑的账号数
- GET  /session/{uid}/config?token=xxx    → 面板 bootstrap（Pusher 公开凭据 + 校验）
- GET  /session/{uid}/history?token=xxx   → 拉最近 N 条消息历史（stm_messages）
- POST /pusher/auth?token=xxx             → Pusher private channel 订阅签名
"""
from __future__ import annotations

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app import config, dashboard_tokens, manager, onboard, realtime
from app.a2a.api_keys import ApiKeyStore
from app.a2a.server import router as a2a_router

app = FastAPI(title="weixin-agent onboarding + dashboard + A2A")
app.include_router(a2a_router)

# 前端经 Vercel serverless 代理调用（服务器到服务器），本不需要 CORS；
# 这里放开以便将来前端直连调试。内部试用阶段先全放开。
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/healthz")
def healthz():
    return {"ok": True, "running_accounts": manager.list_running()}


@app.post("/connect/start")
def connect_start():
    return onboard.start_login()


@app.get("/connect/status")
def connect_status(ticket: str):
    s = onboard.get_status(ticket)
    if s is None:
        return JSONResponse({"status": "unknown"}, status_code=404)
    return s


# ── 旁观面板 API ────────────────────────────────────────────
def _auth_or_401(user_id: str, token: str):
    """Token 换 user_id 并校验必须匹配 URL 中的 user_id。返回 None 表示通过；否则返回 JSONResponse。"""
    who = dashboard_tokens.lookup(token)
    if who is None:
        return JSONResponse({"error": "token 无效或已过期，请在微信里重新发「/看板」"}, status_code=401)
    if who != user_id:
        return JSONResponse({"error": "token 与该 user_id 不匹配"}, status_code=403)
    return None


@app.get("/session/{user_id}/config")
def session_config(user_id: str, token: str = ""):
    """前端 bootstrap：确认 token 合法 + 返回 Pusher 公开 key/cluster 让浏览器初始化。"""
    err = _auth_or_401(user_id, token)
    if err:
        return err
    if not config.pusher_ready():
        return JSONResponse({"error": "Pusher 未配置"}, status_code=503)
    return {
        "user_id": user_id,
        "pusher_key": config.PUSHER_KEY,
        "pusher_cluster": config.PUSHER_CLUSTER,
        "channel": realtime.channel_name(user_id),
    }


@app.get("/session/{user_id}/history")
def session_history(user_id: str, token: str = "", limit: int = 50):
    """拉最近 limit 条短期记忆消息。role ∈ user | assistant | system。"""
    err = _auth_or_401(user_id, token)
    if err:
        return err
    from app.core.memory.short_term import ShortTermMemory
    all_msgs = ShortTermMemory().get_history(user_id)
    tail = all_msgs[-limit:] if len(all_msgs) > limit else all_msgs
    return {
        "user_id": user_id,
        "count": len(tail),
        "messages": [
            {"role": m.role.value, "content": m.content, "metadata": m.metadata}
            for m in tail
        ],
    }


@app.get("/teacher/history")
def teacher_history(user_id: str = "", token: str = ""):
    """看板"跟老师聊" tab 打开时拉一次历史。"""
    err = _auth_or_401(user_id, token)
    if err:
        return err
    from app.agent import teacher
    return {"messages": teacher.history(user_id)}


@app.post("/teacher/chat")
async def teacher_chat(request: Request):
    """向后兼容：老看板版本用这个端点。新版本请用 /panel/chat with conv_id='teacher'。"""
    body = await request.json()
    user_id = body.get("user_id") or ""
    token = body.get("token") or ""
    message = (body.get("message") or "").strip()
    err = _auth_or_401(user_id, token)
    if err:
        return err
    if not message:
        return JSONResponse({"error": "message 不能空"}, status_code=400)

    from app.agent import teacher
    try:
        reply = teacher.handle(user_id, message)
    except Exception as e:  # noqa: BLE001
        import logging as _log
        _log.getLogger(__name__).exception("teacher.handle 失败")
        return JSONResponse({"error": f"老师处理失败: {e}"}, status_code=500)
    return {"reply": reply}


@app.post("/panel/chat")
async def panel_chat(request: Request):
    """看板通用聊天入口。body: {user_id, token, conv_id, message}
    conv_id:
      - 'self'          → 走 runner.handle_text（跟微信一样，但不通过 iLink 发出，只走 realtime）
      - 'teacher'       → 走 teacher.handle
      - 'pair-{other}'  → 以自己身份给对方 Agent 直接发消息（走 handle_agent_message）
    """
    import logging as _log
    body = await request.json()
    user_id = body.get("user_id") or ""
    token = body.get("token") or ""
    conv_id = body.get("conv_id") or ""
    message = (body.get("message") or "").strip()
    err = _auth_or_401(user_id, token)
    if err:
        return err
    if not message:
        return JSONResponse({"error": "message 不能空"}, status_code=400)

    _EVENT = {"thinking": "verbose_thinking",
              "tool_call": "verbose_tool_call",
              "tool_out": "verbose_tool_out"}

    try:
        # ── self：走跟微信一样的 handle_text 但不 push 到 iLink ──
        if conv_id == "self":
            from app.agent.runner import VoiceTaskAgent

            def _wechat_emit(_t):  # 从看板触发的对话，不 push 回 iLink
                pass

            def _realtime_emit(kind, text):
                event = _EVENT.get(kind, "verbose")
                realtime.publish(user_id, event, {"text": text})

            # 前端已经 optimistic 显示了用户消息，仍然推一次给面板一致性
            realtime.publish(user_id, "user_message", {"text": message})
            result = VoiceTaskAgent().handle_text(
                text=message, user_id=user_id,
                emit=_wechat_emit, realtime_emit=_realtime_emit,
            )
            reply = (result.reply or "").strip()
            if reply and result.send:
                realtime.publish(user_id, "assistant_reply", {"text": reply})
            return {"reply": reply}

        # ── teacher：走 teacher.handle ──
        if conv_id == "teacher":
            from app.agent import teacher
            reply = teacher.handle(user_id, message)
            return {"reply": reply}

        # ── claude：走 claude_agent.handle（需 Mac daemon 在线）──
        if conv_id == "claude":
            from app.agent import claude_agent
            if not claude_agent.hub.is_online():
                return JSONResponse(
                    {"error": "Claude 目前不在线（Mac 端 claude_bridge daemon 未连接）"},
                    status_code=503,
                )
            realtime.publish(user_id, "claude_user_message", {"text": message})
            try:
                reply = claude_agent.handle(user_id, message)
            except Exception as e:  # noqa: BLE001
                _log.getLogger(__name__).exception("claude_agent.handle 失败")
                return JSONResponse({"error": f"Claude 处理失败: {e}"}, status_code=500)
            realtime.publish(user_id, "claude_reply", {"text": reply})
            return {"reply": reply}

        # ── pair-{other_uid}：直接给对方 Agent 发一条消息 ──
        if conv_id.startswith("pair-"):
            other_uid = conv_id[len("pair-"):]
            if not other_uid:
                return JSONResponse({"error": "conv_id 缺少 target user_id"}, status_code=400)

            from app.core.memory.bot_users import BotUsersStore
            sender = BotUsersStore().get(user_id)
            target = BotUsersStore().get(other_uid)
            if target is None:
                return JSONResponse({"error": f"没找到对方 Agent: {other_uid}"}, status_code=404)
            sender_display = (sender.display_name if sender and sender.display_name else "某用户")
            target_label = target.display_name or target.user_id

            # 推 agent_conversation 事件到双方 channel（我方 → 对方）
            def _publish_both(payload):
                realtime.publish(user_id, "agent_conversation", payload)
                if not other_uid.startswith("system:"):
                    realtime.publish(other_uid, "agent_conversation", payload)

            _publish_both({
                "round": 0, "direction": "→",
                "from": sender_display, "from_user_id": user_id,
                "to": target_label, "to_user_id": other_uid,
                "text": message,
            })

            # 特殊路径：老师
            if other_uid == "system:teacher":
                from app.agent import teacher
                reply = teacher.handle(user_id, message)
            else:
                from app.agent.runner import VoiceTaskAgent
                reply = VoiceTaskAgent().handle_agent_message(
                    target_user_id=other_uid,
                    from_user_id=user_id,
                    from_display_name=sender_display,
                    message=message,
                    request_authorization=False,
                )
            reply = (reply or "").strip() or "（对方未回复）"

            _publish_both({
                "round": 0, "direction": "←",
                "from": target_label, "from_user_id": other_uid,
                "to": sender_display, "to_user_id": user_id,
                "text": reply,
            })
            return {"reply": reply}

        return JSONResponse({"error": f"unknown conv_id: {conv_id}"}, status_code=400)

    except Exception as e:  # noqa: BLE001
        _log.getLogger(__name__).exception("panel_chat 失败")
        return JSONResponse({"error": f"处理失败: {e}"}, status_code=500)


@app.websocket("/a2a/agents/claude/register")
async def claude_bridge_ws(ws: WebSocket, api_key: str = ""):
    """Mac 上跑的 claude_bridge daemon 连过来。
    连接需要 X-API-Key（或 query api_key=）。
    收 {type: 'result'/'error', task_id, reply/error} 消息，转给 ClaudeAgentHub。
    发 {type: 'task', task_id, prompt} 消息给 daemon 让它调 Claude。"""
    # 兼容两种传 key 的方式：query 或 header
    key = api_key or ws.headers.get("x-api-key", "") or ws.headers.get("X-API-Key", "")
    if not key:
        await ws.close(code=1008, reason="X-API-Key required")
        return
    meta = ApiKeyStore().verify(key)
    if meta is None:
        await ws.close(code=1008, reason="invalid api key")
        return

    await ws.accept()
    import asyncio as _asyncio
    from app.agent import claude_agent as _claude
    loop = _asyncio.get_running_loop()
    _claude.hub.attach(ws, loop)
    try:
        while True:
            msg = await ws.receive_json()
            mtype = msg.get("type")
            task_id = msg.get("task_id") or ""
            if mtype == "result":
                _claude.hub.on_result(
                    task_id,
                    msg.get("reply") or "",
                    session_id=msg.get("session_id"),
                )
            elif mtype == "error":
                _claude.hub.on_error(task_id, msg.get("error") or "unknown")
            elif mtype == "ping":
                # heartbeat
                await ws.send_json({"type": "pong"})
            else:
                # 未知消息，忽略
                pass
    except WebSocketDisconnect:
        pass
    except Exception as e:  # noqa: BLE001
        import logging as _log
        _log.getLogger(__name__).warning("Claude WS 异常: %s", e)
    finally:
        _claude.hub.detach()


@app.post("/pusher/auth")
async def pusher_auth(request: Request, token: str = ""):
    """Pusher private channel 订阅签名端点。

    浏览器 subscribe 前会 POST form-encoded body: socket_id + channel_name；
    我们用 token 换 user_id，验证 channel_name 就是该 user 的 channel，签名后返回。
    手动 parse_qs 避免依赖 python-multipart。"""
    from urllib.parse import parse_qs

    who = dashboard_tokens.lookup(token)
    if who is None:
        return JSONResponse({"error": "token 无效或已过期"}, status_code=401)
    body_bytes = await request.body()
    form = parse_qs(body_bytes.decode("utf-8", errors="ignore"))
    socket_id = (form.get("socket_id") or [""])[0]
    channel_name_val = (form.get("channel_name") or [""])[0]
    if not socket_id or not channel_name_val:
        return JSONResponse({"error": "缺少 socket_id 或 channel_name"}, status_code=400)
    sig = realtime.auth_subscribe(who, socket_id, channel_name_val)
    if sig is None:
        return JSONResponse({"error": "签名失败（channel 与 user 不匹配 或 Pusher 未配置）"},
                            status_code=403)
    return sig
