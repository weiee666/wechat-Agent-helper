# -*- coding: utf-8 -*-
"""语音转写（STT）。

当前策略：用 iLink 自带转写——语音消息 item 里通常已带 voice_item.text，
直接取用，零依赖。STT 是渠道层的输入预处理，不是 LLM 工具：因为 LLM 必须
先拿到文字才能推理，转写应在 agent 之前确定性完成。

后续换 Whisper / 云 STT 时只改这个文件，上层(runner)不动。
"""
from __future__ import annotations

from typing import Optional


def transcribe_ilink_message(msg: dict) -> Optional[str]:
    """从一条 iLink 消息取出文字（文本消息原文 / 语音消息的自带转写）。"""
    for item in msg.get("item_list") or []:
        t = item.get("type")
        if t == 1 and item.get("text_item", {}).get("text"):
            return item["text_item"]["text"]
        if t == 3 and item.get("voice_item", {}).get("text"):
            return item["voice_item"]["text"]
    return None


def transcribe_audio_file(path: str) -> str:
    """预留：本地音频文件 → 文字（接 Whisper 等时实现 silk→wav + STT）。"""
    raise NotImplementedError("暂未接入本地 STT；当前用 iLink 自带转写。")
