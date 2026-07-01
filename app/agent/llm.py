# -*- coding: utf-8 -*-
"""LLM 客户端：DeepSeek（OpenAI 兼容）经 LangChain。

集中创建，供 summarizer 和记忆压缩复用同一个模型。
LangChain ChatModel 同时有 invoke（摘要压缩用）和后续可扩展能力。
"""
from __future__ import annotations

from langchain_openai import ChatOpenAI

from app import config

_llm: ChatOpenAI | None = None


def get_llm() -> ChatOpenAI:
    """返回全局唯一的 DeepSeek ChatModel。"""
    global _llm
    if _llm is None:
        if not config.DEEPSEEK_API_KEY:
            raise RuntimeError("缺少 DEEPSEEK_API_KEY，请在 .env 配置")
        _llm = ChatOpenAI(
            model=config.DEEPSEEK_MODEL,
            base_url=config.DEEPSEEK_BASE_URL,
            api_key=config.DEEPSEEK_API_KEY,
            temperature=0.3,
        )
    return _llm
