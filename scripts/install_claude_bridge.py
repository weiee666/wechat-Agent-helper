#!/usr/bin/env python3
"""install_claude_bridge.py — 在 macOS 上一次性安装 claude_bridge 后台服务。

做的事：
1. 检查环境（Python 3.11+ / websockets / claude CLI 是否装）
2. 询问 A2A API Key（服务器签发的）
3. 写配置到 ~/.claude-bridge/config.json
4. 生成 launchd plist → ~/Library/LaunchAgents/com.weixin-agent.claude-bridge.plist
5. launchctl load 启动
6. 系统开机自启 + 崩溃自动重启

之后：
- daemon 后台永远运行
- 服务器上的 Claude Agent 永远 online
- 更新 API Key 或配置：直接改 ~/.claude-bridge/config.json，然后
    launchctl unload/load 即可

卸载：
    launchctl unload ~/Library/LaunchAgents/com.weixin-agent.claude-bridge.plist
    rm ~/Library/LaunchAgents/com.weixin-agent.claude-bridge.plist
    rm -r ~/.claude-bridge
"""
from __future__ import annotations

import getpass
import json
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

LABEL = "com.weixin-agent.claude-bridge"
DEFAULT_SERVER = "ws://43.142.81.135:7997/a2a/agents/claude/register"


def die(msg: str, code: int = 1):
    print(f"✗ {msg}", file=sys.stderr)
    sys.exit(code)


def find_or_create_daemon_python() -> Path:
    """找一个能跑 daemon 的 Python（依赖 websockets）。优先项目已有 venv；
    没有就在 ~/.claude-bridge/venv 建一个专用 venv。返回 python 可执行文件路径。"""
    project_root = Path(__file__).resolve().parent.parent
    for name in (".venv", "venv"):
        p = project_root / name / "bin" / "python3"
        if p.exists():
            print(f"✓ 用项目 venv: {p}")
            return p
    dedicated = Path.home() / ".claude-bridge" / "venv"
    if not (dedicated / "bin" / "python3").exists():
        print(f"建专用 venv: {dedicated}…")
        subprocess.check_call([sys.executable, "-m", "venv", str(dedicated)])
    return dedicated / "bin" / "python3"


def ensure_websockets(python_bin: Path) -> None:
    ok = subprocess.run(
        [str(python_bin), "-c", "import websockets"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    ).returncode == 0
    if ok:
        return
    print(f"装 websockets 到 {python_bin}…")
    subprocess.check_call([str(python_bin), "-m", "pip", "install", "websockets"])


def check_env():
    if platform.system() != "Darwin":
        die("这个脚本目前只支持 macOS（launchd 是 Mac 特有的）。Linux 用户可参考 scripts/claude_bridge.py 手动跑 + systemd。")
    if sys.version_info < (3, 11):
        die(f"Python 3.11+ 才行，你当前 {sys.version.split()[0]}。装个新的：brew install python@3.13")
    python_bin = find_or_create_daemon_python()
    ensure_websockets(python_bin)
    cli_path = shutil.which("claude")
    if not cli_path:
        print("⚠ PATH 里找不到 claude CLI（Claude Code 应用装的是 GUI，但也带 claude 命令）")
        print("  你可能需要在 Claude Code 应用里设置 → Enable 'claude' CLI 才能生效")
        cli_path = input("  或者输入 claude 可执行文件的绝对路径（回车跳过用默认 'claude'）: ").strip() or "claude"
    return cli_path, python_bin


def prompt_api_key():
    print()
    print("需要一把服务器签发的 A2A API Key（用来向 /a2a/agents/claude/register 认证）。")
    print("如果不确定：找管理员要，或者 SSH 到服务器跑 ApiKeyStore().issue(...)。")
    key = getpass.getpass("API Key（输入时不显示）: ").strip()
    if not key:
        die("API Key 不能空")
    return key


def write_config(cfg_dir: Path, api_key: str, cli_path: str):
    cfg_dir.mkdir(parents=True, exist_ok=True)
    cfg_path = cfg_dir / "config.json"
    cfg = {
        "server": DEFAULT_SERVER,
        "api_key": api_key,
        "cli": cli_path,
        "timeout": 280,
    }
    cfg_path.write_text(json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8")
    os.chmod(cfg_path, 0o600)
    print(f"✓ 配置写入 {cfg_path}")
    return cfg_path


def write_plist(plist_dir: Path, python_bin: str, script_path: Path, cfg_dir: Path) -> Path:
    plist_dir.mkdir(parents=True, exist_ok=True)
    plist_path = plist_dir / f"{LABEL}.plist"
    log_dir = cfg_dir / "logs"
    log_dir.mkdir(exist_ok=True)
    plist_content = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key><string>{LABEL}</string>
    <key>ProgramArguments</key>
    <array>
        <string>{python_bin}</string>
        <string>{script_path}</string>
    </array>
    <key>WorkingDirectory</key><string>{script_path.parent.parent}</string>
    <key>RunAtLoad</key><true/>
    <key>KeepAlive</key><true/>
    <key>StandardOutPath</key><string>{log_dir / 'stdout.log'}</string>
    <key>StandardErrorPath</key><string>{log_dir / 'stderr.log'}</string>
    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key><string>/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin</string>
    </dict>
</dict>
</plist>
"""
    plist_path.write_text(plist_content, encoding="utf-8")
    os.chmod(plist_path, 0o644)
    print(f"✓ launchd plist 写入 {plist_path}")
    return plist_path


def launchctl_reload(plist_path: Path):
    # 先 unload（若已装过），忽略错误
    subprocess.run(["launchctl", "unload", str(plist_path)],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    result = subprocess.run(["launchctl", "load", str(plist_path)],
                            capture_output=True, text=True)
    if result.returncode != 0:
        print(f"⚠ launchctl load 失败: {result.stderr.strip()}", file=sys.stderr)
        return False
    return True


def check_running():
    result = subprocess.run(["launchctl", "list", LABEL],
                            capture_output=True, text=True)
    return result.returncode == 0


def main():
    print("=" * 60)
    print("claude_bridge 后台服务安装")
    print("=" * 60)

    cli_path, python_bin = check_env()
    api_key = prompt_api_key()

    cfg_dir = Path.home() / ".claude-bridge"
    write_config(cfg_dir, api_key, cli_path)

    script_path = Path(__file__).resolve().parent / "claude_bridge.py"
    if not script_path.exists():
        die(f"找不到 claude_bridge.py：{script_path}")
    plist_dir = Path.home() / "Library" / "LaunchAgents"
    plist_path = write_plist(plist_dir, str(python_bin), script_path, cfg_dir)

    print()
    print("启动 daemon…")
    ok = launchctl_reload(plist_path)
    if not ok:
        die("启动失败；请手动检查 launchctl 输出。")

    print()
    print("✓ 安装完成！")
    print()
    print(f"  日志:      {cfg_dir / 'logs' / 'stdout.log'}")
    print(f"           {cfg_dir / 'logs' / 'stderr.log'}")
    print(f"  配置:      {cfg_dir / 'config.json'}")
    print(f"  plist:     {plist_path}")
    print()
    print("现在服务器上 Claude Agent 应该几秒内变为 running。")
    print()
    print("卸载:")
    print(f"  launchctl unload {plist_path}")
    print(f"  rm {plist_path}")
    print(f"  rm -r {cfg_dir}")


if __name__ == "__main__":
    main()
