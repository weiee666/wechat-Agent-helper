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

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app import config, dashboard_tokens, manager, onboard, realtime

app = FastAPI(title="weixin-agent onboarding + dashboard")

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
