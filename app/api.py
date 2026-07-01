# -*- coding: utf-8 -*-
"""自助开通 HTTP API（FastAPI）。被 Vercel serverless 代理调用。

- POST /connect/start        → 现生成一张新二维码，返回 {ticket, status, qr_svg}
- GET  /connect/status?ticket→ 该会话当前状态；confirmed 时账号已热起
- GET  /healthz              → 健康检查 + 在跑的账号数
"""
from __future__ import annotations

from fastapi import FastAPI, Header, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app import config, manager, onboard
from app.core.memory.directory import EmployeeDirectory

app = FastAPI(title="weixin-agent onboarding")

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


@app.post("/directory/sync")
async def directory_sync(request: Request, x_sync_token: str = Header(default="")):
    """接收已解析好的通讯录条目（JSON），整表替换。"""
    if not config.DIRECTORY_SYNC_TOKEN or x_sync_token != config.DIRECTORY_SYNC_TOKEN:
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    body = await request.json()
    n = EmployeeDirectory().replace_all(body.get("entries") or [])
    return {"ok": True, "count": n}


@app.post("/directory/upload")
async def directory_upload(request: Request, x_sync_token: str = Header(default=""),
                           x_filename: str = Header(default="")):
    """接收上传的通讯录文件原始字节（CSV/xlsx），服务器解析后整表替换。

    带 header  X-Sync-Token（校验）和 X-Filename（判断格式，可空）。
    """
    if not config.DIRECTORY_SYNC_TOKEN or x_sync_token != config.DIRECTORY_SYNC_TOKEN:
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    content = await request.body()
    if not content:
        return JSONResponse({"error": "空文件"}, status_code=400)
    from app.core.table_parse import parse_table
    try:
        entries = parse_table(content, x_filename)
    except Exception as e:  # noqa: BLE001
        return JSONResponse({"error": f"解析失败：{e}"}, status_code=400)
    if not entries:
        return JSONResponse({"error": "没解析出任何含邮箱的行，检查下表头/内容"}, status_code=400)
    n = EmployeeDirectory().replace_all(entries)
    return {"ok": True, "count": n}
