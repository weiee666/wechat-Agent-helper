# -*- coding: utf-8 -*-
"""Google Drive 员工通讯录同步。

用服务账号只读访问「指定文件夹」，取里面最新的那个表格（按文件类型识别，不认文件名），
解析姓名/邮箱，整表同步进全局 employee_directory。支持 Shared Drive。
"""
from __future__ import annotations

import csv
import io
import logging
import threading

from app import config
from app.core.memory.directory import EmployeeDirectory

logger = logging.getLogger("weixin-agent.gdrive")

_SCOPES = ["https://www.googleapis.com/auth/drive.readonly"]
_SHEET_MIMES = {
    "text/csv",
    "application/vnd.ms-excel",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/vnd.google-apps.spreadsheet",
}


def _service():
    from google.oauth2 import service_account
    from googleapiclient.discovery import build
    creds = service_account.Credentials.from_service_account_file(config.GDRIVE_KEY_FILE, scopes=_SCOPES)
    return build("drive", "v3", credentials=creds, cache_discovery=False)


def _latest_table_file(svc, folder_id: str) -> dict | None:
    files = svc.files().list(
        q=f"'{folder_id}' in parents and trashed=false",
        fields="files(id,name,mimeType,modifiedTime)",
        orderBy="modifiedTime desc",
        supportsAllDrives=True, includeItemsFromAllDrives=True,
    ).execute().get("files", [])
    for f in files:  # 已按修改时间倒序，取第一个表格类文件
        if f["mimeType"] in _SHEET_MIMES:
            return f
    return None


def _download_rows(svc, f: dict) -> list[list[str]]:
    mime = f["mimeType"]
    if mime == "application/vnd.google-apps.spreadsheet":
        content = svc.files().export(fileId=f["id"], mimeType="text/csv").execute()
        return _parse_csv(content)
    content = svc.files().get_media(fileId=f["id"], supportsAllDrives=True).execute()
    if mime == "text/csv":
        return _parse_csv(content)
    return _parse_xlsx(content)  # xlsx / xls


def _parse_csv(content: bytes) -> list[list[str]]:
    text = content.decode("utf-8-sig", errors="replace")
    return [row for row in csv.reader(io.StringIO(text))]


def _parse_xlsx(content: bytes) -> list[list[str]]:
    from openpyxl import load_workbook
    wb = load_workbook(io.BytesIO(content), read_only=True, data_only=True)
    ws = wb.active
    return [[("" if c is None else str(c)) for c in row] for row in ws.iter_rows(values_only=True)]


def _rows_to_entries(rows: list[list[str]]) -> list[dict]:
    """灵活识别表头：First/Last Name + Email，或单个 姓名/Name + 邮箱/Email。"""
    rows = [r for r in rows if any((c or "").strip() for c in r)]
    if not rows:
        return []
    header = [(h or "").strip().lower() for h in rows[0]]

    def find(*keys):
        for i, h in enumerate(header):
            if any(k in h for k in keys):
                return i
        return -1

    i_email = find("email", "邮箱", "e-mail", "mail")
    i_first = find("first name", "名字", "given name")
    i_last = find("last name", "姓氏", "family name")
    i_full = find("full name", "姓名", "name") if i_first < 0 else -1

    # 邮箱列找不到：退而取"值里含@最多"的列
    if i_email < 0:
        best, best_n = -1, 0
        for ci in range(len(header)):
            n = sum(1 for r in rows[1:] if "@" in (r[ci] if ci < len(r) else ""))
            if n > best_n:
                best, best_n = ci, n
        i_email = best

    entries = []
    for r in rows[1:]:
        def cell(i):
            return (r[i].strip() if 0 <= i < len(r) and r[i] else "")
        email = cell(i_email)
        if not email or "@" not in email:
            continue
        if i_first >= 0 or i_last >= 0:
            first, last = cell(i_first), cell(i_last)
            name = f"{first} {last}".strip()
            aliases = [x for x in (first, last) if x]
        else:
            name = cell(i_full) or email.split("@")[0]
            aliases = []
        entries.append({"name": name or email.split("@")[0], "email": email, "aliases": aliases})
    return entries


def sync_directory() -> int:
    """从 Drive 拉最新表 → 整表同步进 employee_directory。返回条数；未配置返回 -1。"""
    if not config.gdrive_ready():
        logger.info("Drive 通讯录未配置（缺密钥或文件夹ID），跳过同步")
        return -1
    svc = _service()
    f = _latest_table_file(svc, config.GDRIVE_FOLDER_ID)
    if not f:
        raise RuntimeError("指定文件夹里没有表格文件（csv/xlsx/Google表格）")
    rows = _download_rows(svc, f)
    entries = _rows_to_entries(rows)
    n = EmployeeDirectory().replace_all(entries)
    logger.info("通讯录已同步：文件 %s → %d 条", f["name"], n)
    return n


def start_periodic_sync(stop: threading.Event) -> None:
    """后台线程：启动先同步一次，之后每 GDRIVE_SYNC_MINUTES 分钟同步一次。"""
    def loop():
        while not stop.is_set():
            try:
                sync_directory()
            except Exception as e:  # noqa: BLE001
                logger.warning("通讯录同步失败：%s", e)
            if stop.wait(config.GDRIVE_SYNC_MINUTES * 60):
                break
    threading.Thread(target=loop, daemon=True, name="gdrive-sync").start()
