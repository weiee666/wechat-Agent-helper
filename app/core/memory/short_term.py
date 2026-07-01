# -*- coding: utf-8 -*-
"""短期记忆：滑动窗口 + 超量时 LLM 摘要压缩。

—— 移植自 autoweixin app/core/memory/short_term.py ——
压缩算法（窗口判断 / token 计数 / 留尾 / LLM 摘要老消息）一字未改，
只把存储后端从 Redis(async) 换成 SQLite(sync)，LLM 调用从 ainvoke 换成 invoke。
"""
from __future__ import annotations

import json
import logging
from typing import Any

from app.core.memory.store import get_conn
from app.models.enums import MessageRole
from app.models.schemas import Message

logger = logging.getLogger(__name__)

try:
    import tiktoken
    _ENCODING = tiktoken.get_encoding("cl100k_base")
except Exception:  # noqa: BLE001
    _ENCODING = None


class ShortTermMemory:
    """SQLite 滑动窗口 + 自动摘要压缩。

    :param llm: 用于摘要的 LLM，需实现同步 ``invoke``（LangChain ChatModel 即可）。
                为 None 时压缩退回截断。
    """

    def __init__(self, llm: Any = None, window_size: int = 20, max_tokens: int = 4000) -> None:
        self._llm = llm
        self.window_size = window_size
        self.max_tokens = max_tokens

    # ── token 估算（与原版一致）─────────────────────────────
    def _count_tokens(self, text: str) -> int:
        if _ENCODING is None:
            return max(1, len(text) // 4)
        return len(_ENCODING.encode(text))

    # ── 序列化 ──────────────────────────────────────────────
    @staticmethod
    def _row_to_msg(row) -> Message:
        try:
            return Message(
                role=MessageRole(row["role"]),
                content=row["content"],
                metadata=json.loads(row["metadata"] or "{}"),
            )
        except (ValueError, json.JSONDecodeError) as e:
            logger.warning("反序列化消息失败，使用占位: %s", e)
            return Message(role=MessageRole.SYSTEM, content=row["content"], metadata={"error": "decode"})

    # ── 读 / 写 ─────────────────────────────────────────────
    def get_history(self, session_id: str) -> list[Message]:
        """按时间顺序读取该会话全部消息。"""
        conn = get_conn()
        try:
            rows = conn.execute(
                "SELECT role, content, metadata FROM stm_messages WHERE session_id=? ORDER BY seq",
                (session_id,),
            ).fetchall()
            return [self._row_to_msg(r) for r in rows]
        finally:
            conn.close()

    def add_message(self, session_id: str, message: Message) -> None:
        """追加一条消息，并视需要触发压缩。"""
        conn = get_conn()
        try:
            cur = conn.execute(
                "SELECT COALESCE(MAX(seq), -1) + 1 AS nxt FROM stm_messages WHERE session_id=?",
                (session_id,),
            ).fetchone()
            seq = cur["nxt"]
            conn.execute(
                "INSERT INTO stm_messages(session_id, seq, role, content, metadata) VALUES (?,?,?,?,?)",
                (session_id, seq, message.role.value, message.content,
                 json.dumps(message.metadata, ensure_ascii=False)),
            )
            conn.commit()
        finally:
            conn.close()
        self._compress_if_needed(session_id)

    # ── 压缩（算法与原版一致）────────────────────────────────
    def _compress_if_needed(self, session_id: str) -> None:
        msgs = self.get_history(session_id)
        n = len(msgs)
        total_tokens = sum(self._count_tokens(m.content) for m in msgs)

        if n <= self.window_size and total_tokens <= self.max_tokens:
            return
        if len(msgs) < 2:
            return

        # 保留尾部若干条，对其余做摘要；keep 不超过当前长度减一
        keep = max(2, self.window_size // 2)
        if keep >= len(msgs):
            keep = len(msgs) - 1
        to_summarize = msgs[:-keep]
        tail = msgs[-keep:]

        summary_text = self._summarize_messages(to_summarize)
        summary_msg = Message(
            role=MessageRole.SYSTEM,
            content=f"[历史摘要]\n{summary_text}",
            metadata={"compressed_from": len(to_summarize)},
        )

        combined = [summary_msg, *tail]
        conn = get_conn()
        try:
            conn.execute("DELETE FROM stm_messages WHERE session_id=?", (session_id,))
            for i, m in enumerate(combined):
                conn.execute(
                    "INSERT INTO stm_messages(session_id, seq, role, content, metadata) VALUES (?,?,?,?,?)",
                    (session_id, i, m.role.value, m.content,
                     json.dumps(m.metadata, ensure_ascii=False)),
                )
            conn.commit()
        finally:
            conn.close()

        logger.info("会话 %s 已压缩：摘要 %d 条历史，保留 %d 条", session_id, len(to_summarize), len(tail))

    def _summarize_messages(self, messages: list[Message]) -> str:
        """调用 LLM 把老消息压成摘要；无 LLM 时退回截断。"""
        lines = [f"{m.role.value}: {m.content}" for m in messages]
        prompt = (
            "请将以下对话压缩为简洁中文摘要，保留关键事实与用户意图：\n\n"
            + "\n".join(lines)
        )
        invoke = getattr(self._llm, "invoke", None)
        if not callable(invoke):
            return "\n".join(lines)[:2000]
        try:
            raw = invoke(prompt)
        except Exception as e:  # noqa: BLE001
            logger.exception("摘要 LLM 调用失败: %s", e)
            return "\n".join(lines)[:2000]

        if hasattr(raw, "content"):
            return str(getattr(raw, "content", "")).strip()
        if isinstance(raw, dict) and "content" in raw:
            return str(raw["content"]).strip()
        return str(raw).strip()
