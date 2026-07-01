# -*- coding: utf-8 -*-
"""把转写文字压缩成 ≤100 字摘要。提示词外置在 prompts/summarize.txt，可随时编辑。"""
from __future__ import annotations

from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate

from app import config
from app.agent.llm import get_llm


def load_summarize_prompt() -> str:
    """读取当前总结提示词（外部可编辑，支撑后续"半自动自我进化"）。"""
    return config.SUMMARIZE_PROMPT_FILE.read_text(encoding="utf-8")


def build_chain():
    prompt = ChatPromptTemplate.from_template(load_summarize_prompt())
    return prompt | get_llm() | StrOutputParser()


def summarize(transcript: str) -> str:
    """转写文字 → ≤100 字摘要。"""
    return build_chain().invoke({"transcript": transcript}).strip()
