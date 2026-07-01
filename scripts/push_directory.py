#!/usr/bin/env python3
"""在「能连 Google 的机器」（如 Mac）上拉 Drive 员工表，推送到服务器。

服务器在国内连不上 Google，所以由这台机器拉表 → POST /directory/sync。

用法（在项目根，venv 里）：
    GDRIVE_KEY_FILE=.../gdrive-key.json \
    GDRIVE_FOLDER_ID=0AHYmy-BNuEvVUk9PVA \
    DIRECTORY_SYNC_TOKEN=xxx \
    SERVER_BASE=http://43.142.81.135:8080 \
    python -m scripts.push_directory
"""
import os
import sys

import requests

from app.integrations import gdrive


def main() -> int:
    server = os.environ.get("SERVER_BASE", "http://43.142.81.135:8080").rstrip("/")
    token = os.environ.get("DIRECTORY_SYNC_TOKEN", "")
    if not token:
        print("缺 DIRECTORY_SYNC_TOKEN")
        return 1

    svc = gdrive._service()
    f = gdrive._latest_table_file(svc, os.environ["GDRIVE_FOLDER_ID"])
    if not f:
        print("文件夹里没有表格文件")
        return 1
    rows = gdrive._download_rows(svc, f)
    entries = gdrive._rows_to_entries(rows)
    print(f"从 Drive 拉到 {len(entries)} 人（文件：{f['name']}），推送到 {server} ...")

    r = requests.post(f"{server}/directory/sync", json={"entries": entries},
                      headers={"X-Sync-Token": token}, timeout=30)
    print("服务器返回:", r.status_code, r.text[:200])
    return 0 if r.ok else 1


if __name__ == "__main__":
    sys.exit(main())
