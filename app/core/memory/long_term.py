# -*- coding: utf-8 -*-
"""长期记忆：SQLite 结构化存储 + 关键词召回。

—— 替代 autoweixin 的 Milvus 向量召回 ——
对"语音转任务"这类场景，历史任务/事实是结构化数据，关键词召回够用、更可控。
将来要语义召回，可在这里换成嵌入式向量库(LanceDB / sqlite-vec)，接口不变。
"""
from __future__ import annotations

import json
import logging
import re
import uuid
from datetime import datetime

from app.core.memory.store import get_conn
from app.models.schemas import MemoryItem

logger = logging.getLogger(__name__)


class LongTermMemory:
    """SQLite 持久化的长期记忆。"""

    def store(self, session_id: str, content: str, metadata: dict | None = None) -> str:
        """存一条长期记忆，返回其 id。"""
        item_id = uuid.uuid4().hex
        conn = get_conn()
        try:
            conn.execute(
                "INSERT INTO ltm_items(id, session_id, content, metadata, created_at) VALUES (?,?,?,?,?)",
                (item_id, session_id, content,
                 json.dumps(metadata or {}, ensure_ascii=False),
                 datetime.now().isoformat(timespec="seconds")),
            )
            conn.commit()
        finally:
            conn.close()
        return item_id

    def recall(self, query: str, session_id: str, top_k: int = 5) -> list[MemoryItem]:
        """按关键词命中数排序召回；无命中则退回最近若干条。"""
        conn = get_conn()
        try:
            rows = conn.execute(
                "SELECT id, content, metadata FROM ltm_items WHERE session_id=? ORDER BY created_at DESC LIMIT 200",
                (session_id,),
            ).fetchall()
        finally:
            conn.close()

        tokens = [t for t in re.split(r"\s+|[，,。.；;]", query or "") if len(t) >= 2]
        scored: list[tuple[float, MemoryItem]] = []
        for r in rows:
            hits = sum(1 for t in tokens if t in r["content"]) if tokens else 0
            item = MemoryItem(
                id=r["id"], content=r["content"], score=float(hits),
                metadata=json.loads(r["metadata"] or "{}"),
            )
            scored.append((hits, item))

        # 有命中按命中数排序；全 0（无关键词/无命中）则保持最近优先
        if any(h > 0 for h, _ in scored):
            scored.sort(key=lambda x: x[0], reverse=True)
        return [it for _, it in scored[:top_k]]
