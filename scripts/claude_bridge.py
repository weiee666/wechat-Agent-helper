#!/usr/bin/env python3
"""claude_bridge.py — 在用户 Mac 上运行，把本地 Claude Code CLI 接入服务器 Agent 体系。

架构：
    Mac ─── WebSocket ───▶ 腾讯云服务器（/a2a/agents/claude/register）
                          服务器把要问 Claude 的任务通过 WS 推给 Mac，
                          Mac 调 `claude -p "..."` 处理，把结果通过 WS 推回。

安装：
    pip3 install websockets

配置：
    export CLAUDE_BRIDGE_SERVER=ws://43.142.81.135:7997/a2a/agents/claude/register
    export CLAUDE_BRIDGE_API_KEY=<你的 A2A API Key>
    export CLAUDE_BRIDGE_CLI=claude   # 或者 /usr/local/bin/claude 的绝对路径
    export CLAUDE_BRIDGE_TIMEOUT=280  # 秒；应小于服务器端 TASK_TIMEOUT_SECONDS=300

运行：
    python3 claude_bridge.py

行为：
- 启动后主动连服务器 WS
- 断线自动重连（指数退避）
- 收到 task 消息 → 调 claude CLI → 把 stdout 作为 result 回传
- claude 出错 → 回传 error 消息
- 每 30 秒心跳一次（发 ping）
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import signal
import subprocess
import sys

try:
    import websockets
except ImportError:
    print("需要先安装 websockets：pip3 install websockets", file=sys.stderr)
    sys.exit(1)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("claude-bridge")

SERVER = os.getenv("CLAUDE_BRIDGE_SERVER",
                   "ws://43.142.81.135:7997/a2a/agents/claude/register")
API_KEY = os.getenv("CLAUDE_BRIDGE_API_KEY", "").strip()
CLAUDE_CLI = os.getenv("CLAUDE_BRIDGE_CLI", "claude")
TIMEOUT = int(os.getenv("CLAUDE_BRIDGE_TIMEOUT", "280"))

HEARTBEAT_SECONDS = 30
RECONNECT_MIN = 2
RECONNECT_MAX = 30


async def run_claude(prompt: str) -> tuple[bool, str]:
    """调 `claude -p "prompt"` 并返回 (成功, stdout 或 stderr)."""
    cli = shutil.which(CLAUDE_CLI) or CLAUDE_CLI
    log.info("调 claude: %s -p <%d 字符>", cli, len(prompt))
    try:
        proc = await asyncio.create_subprocess_exec(
            cli, "-p", prompt,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=TIMEOUT)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            return False, f"claude 超时 ({TIMEOUT}s)"
        if proc.returncode == 0:
            out = (stdout or b"").decode("utf-8", errors="replace").strip()
            return True, out or "（claude 未输出内容）"
        err = (stderr or b"").decode("utf-8", errors="replace").strip()
        return False, err or f"claude 退出码 {proc.returncode}"
    except FileNotFoundError:
        return False, f"找不到 claude CLI：{cli}，请检查 CLAUDE_BRIDGE_CLI 环境变量或 PATH"
    except Exception as e:  # noqa: BLE001
        return False, f"启动 claude 失败：{e}"


async def handle_task(ws, msg: dict) -> None:
    task_id = msg.get("task_id") or ""
    prompt = msg.get("prompt") or ""
    if not task_id or not prompt:
        await ws.send(json.dumps({"type": "error", "task_id": task_id,
                                   "error": "missing task_id or prompt"}))
        return
    ok, output = await run_claude(prompt)
    payload = {"type": "result" if ok else "error", "task_id": task_id}
    if ok:
        payload["reply"] = output
    else:
        payload["error"] = output
    await ws.send(json.dumps(payload))


async def heartbeat(ws):
    while True:
        await asyncio.sleep(HEARTBEAT_SECONDS)
        try:
            await ws.send(json.dumps({"type": "ping"}))
        except Exception:  # noqa: BLE001
            return


async def one_session():
    url = SERVER
    if "?" not in url:
        url += f"?api_key={API_KEY}"
    else:
        url += f"&api_key={API_KEY}"
    log.info("连服务器 %s", SERVER)
    async with websockets.connect(url, ping_interval=30, ping_timeout=15) as ws:
        log.info("✓ 已连接。等任务…")
        hb = asyncio.create_task(heartbeat(ws))
        try:
            async for raw in ws:
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                mtype = msg.get("type")
                if mtype == "task":
                    # 并行处理不同 task；一 Mac 一 claude 也是可以的（不同 session）
                    asyncio.create_task(handle_task(ws, msg))
                elif mtype == "pong":
                    pass
        finally:
            hb.cancel()


async def main():
    if not API_KEY:
        log.error("必须设置 CLAUDE_BRIDGE_API_KEY 环境变量（服务器签发的 A2A API Key）")
        sys.exit(2)
    if not shutil.which(CLAUDE_CLI):
        log.warning("PATH 里找不到 %s；如果它在别的位置，请设 CLAUDE_BRIDGE_CLI", CLAUDE_CLI)

    delay = RECONNECT_MIN
    while True:
        try:
            await one_session()
            log.info("连接正常关闭；即将重连")
            delay = RECONNECT_MIN
        except (ConnectionRefusedError, websockets.exceptions.InvalidStatusCode,
                websockets.exceptions.WebSocketException, OSError) as e:
            log.warning("连接失败: %s；%d 秒后重试", e, delay)
        except Exception as e:  # noqa: BLE001
            log.warning("会话异常: %s；%d 秒后重试", e, delay)
        await asyncio.sleep(delay)
        delay = min(delay * 2, RECONNECT_MAX)


if __name__ == "__main__":
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(sig, lambda *_: sys.exit(0))
        except Exception:
            pass
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
