#!/usr/bin/env python3
"""claude_bridge.py — 在用户 Mac 上后台运行，把本地 Claude Code CLI 接入服务器 Agent 体系。

架构：
    Mac ─── WebSocket ───▶ 腾讯云服务器（/a2a/agents/claude/register）
                          服务器把要问 Claude 的任务通过 WS 推给 Mac，
                          Mac 调 claude CLI（headless 模式）处理，结果通过 WS 推回。

session 延续（关键）：
- daemon 用 `claude --print --output-format json` 调 Claude Code CLI
- 首次调用拿到返回 JSON 里的 session_id
- 服务器端记住 per-user session_id；后续调用带上 session_id
- daemon 用 `--resume <session_id>` 让 Claude 接续同一对话上下文

安装（推荐）：
    python3 scripts/install_claude_bridge.py
    → 装 launchd 后台服务；系统开机自启；一次配置永久生效

手动运行（调试用）：
    export CLAUDE_BRIDGE_API_KEY=<你的 A2A API Key>
    python3 scripts/claude_bridge.py

配置来源（优先级从高到低）：
    1. ~/.claude-bridge/config.json  {api_key, server, cli, timeout}
    2. 环境变量 CLAUDE_BRIDGE_*
    3. 内置默认值

行为：
- 启动后主动连服务器 WS，带 API Key
- 断线自动重连（指数退避 2s → 30s）
- 收到 task {prompt, session_id?} → 跑 claude CLI → 回传 {reply, session_id}
- 每 30 秒心跳 ping
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
from pathlib import Path

try:
    import websockets
except ImportError:
    print("需要先安装 websockets：pip3 install websockets", file=sys.stderr)
    sys.exit(1)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("claude-bridge")


def _load_config() -> dict:
    """先读 ~/.claude-bridge/config.json，字段未填的走环境变量兜底。"""
    cfg_path = Path.home() / ".claude-bridge" / "config.json"
    cfg = {}
    if cfg_path.exists():
        try:
            cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
        except Exception as e:  # noqa: BLE001
            log.warning("读 %s 失败: %s；改用环境变量", cfg_path, e)
    def _pick(cfg_key, env_key, default=""):
        return (cfg.get(cfg_key) or os.getenv(env_key, default)).strip() if isinstance(cfg.get(cfg_key) or os.getenv(env_key, default), str) else (cfg.get(cfg_key) if cfg.get(cfg_key) is not None else os.getenv(env_key, default))
    return {
        "server": _pick("server", "CLAUDE_BRIDGE_SERVER",
                        "ws://43.142.81.135:7997/a2a/agents/claude/register"),
        "api_key": _pick("api_key", "CLAUDE_BRIDGE_API_KEY", ""),
        "cli": _pick("cli", "CLAUDE_BRIDGE_CLI", "claude"),
        "timeout": int(cfg.get("timeout") or os.getenv("CLAUDE_BRIDGE_TIMEOUT", "280")),
    }


CONFIG = _load_config()
SERVER = CONFIG["server"]
API_KEY = CONFIG["api_key"]
CLAUDE_CLI = CONFIG["cli"]
TIMEOUT = CONFIG["timeout"]

HEARTBEAT_SECONDS = 30
RECONNECT_MIN = 2
RECONNECT_MAX = 30


async def run_claude(prompt: str, session_id: str | None = None) -> tuple[bool, str, str | None]:
    """调 claude CLI（headless + JSON 输出）。返回 (成功, reply 或 error, new_session_id or None)。

    首次调用不传 session_id；后续调用传上次拿到的 session_id 让 Claude 接续对话。
    """
    cli = shutil.which(CLAUDE_CLI) or CLAUDE_CLI
    args = [cli, "--print", "--output-format", "json"]
    if session_id:
        args.extend(["--resume", session_id])
    args.append(prompt)
    log.info("调 claude: %s%s <%d 字符 prompt>",
             cli, f" --resume {session_id[:8]}…" if session_id else "", len(prompt))
    try:
        proc = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=TIMEOUT)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            return False, f"claude 超时 ({TIMEOUT}s)", None
        raw_out = (stdout or b"").decode("utf-8", errors="replace").strip()
        raw_err = (stderr or b"").decode("utf-8", errors="replace").strip()
        if proc.returncode == 0:
            # 解析 JSON：Claude Code 的 --output-format json 通常返回 {result: "...", session_id: "..."}
            try:
                data = json.loads(raw_out)
            except json.JSONDecodeError:
                return True, raw_out or "（claude 无输出）", None
            reply = data.get("result") or data.get("text") or data.get("content") or ""
            new_sid = data.get("session_id") or session_id
            return True, str(reply).strip() or "（claude 无输出）", new_sid
        # 非零退出码：session_id 不存在时 --resume 会失败——降级重试无 session
        if session_id and ("session" in raw_err.lower() or "not found" in raw_err.lower()):
            log.warning("session %s 无效，降级为新对话重试", session_id[:8])
            return await run_claude(prompt, session_id=None)
        return False, raw_err or f"claude 退出码 {proc.returncode}", None
    except FileNotFoundError:
        return False, f"找不到 claude CLI：{cli}，请检查配置或 PATH", None
    except Exception as e:  # noqa: BLE001
        return False, f"启动 claude 失败：{e}", None


async def handle_task(ws, msg: dict) -> None:
    task_id = msg.get("task_id") or ""
    prompt = msg.get("prompt") or ""
    session_id = msg.get("session_id") or None
    if not task_id or not prompt:
        await ws.send(json.dumps({"type": "error", "task_id": task_id,
                                   "error": "missing task_id or prompt"}))
        return
    ok, output, new_session_id = await run_claude(prompt, session_id=session_id)
    payload = {"type": "result" if ok else "error", "task_id": task_id}
    if ok:
        payload["reply"] = output
        if new_session_id:
            payload["session_id"] = new_session_id
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
        log.error("必须配置 api_key（~/.claude-bridge/config.json 或 CLAUDE_BRIDGE_API_KEY 环境变量）")
        log.error("使用 python3 scripts/install_claude_bridge.py 一键安装可自动配置")
        sys.exit(2)
    if not shutil.which(CLAUDE_CLI):
        log.warning("PATH 里找不到 %s；如果它在别的位置，请在配置里设 cli", CLAUDE_CLI)

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
