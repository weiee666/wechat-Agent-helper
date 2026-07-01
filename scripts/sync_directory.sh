#!/bin/bash
# 一键：从 Google Drive 拉员工表 → 推送到服务器通讯录。
# 改了 Drive 里的表之后，跑一下这个就同步。
set -e
cd "/Users/admin/Desktop/python project/weixin-agent"
export GDRIVE_KEY_FILE="/Users/admin/Desktop/python project/账号密码/weixin-agent-500906-f39f4e5581fc.json"
export GDRIVE_FOLDER_ID="0AHYmy-BNuEvVUk9PVA"
export SERVER_BASE="http://43.142.81.135:8080"
export DIRECTORY_SYNC_TOKEN="$(grep '^DIRECTORY_SYNC_TOKEN=' .env | cut -d= -f2)"
./.venv/bin/python -m scripts.push_directory
