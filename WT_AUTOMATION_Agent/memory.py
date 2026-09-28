# encoding: utf-8
"""多轮对话记忆与历史压缩。

当对话轮次较多时，把较早的对话内容用 LLM 压缩成一份摘要，
仅在上下文中保留最近若干轮原文，从而在有限 token 内维持长程记忆。
"""
from __future__ import annotations

from typing import Any, Callable

from WT_AUTOMATION_Agent.history_store import (
    load_conversation,
    save_conversation,
    get_messages_for_llm,
)

DEFAULT_RECENT_TURNS = 6

SUMMARY_SYSTEM_PROMPT = (
    "你是一个对话摘要器。请阅读以下对话记录，用简洁的中文提炼：\n"
    "1) 用户的核心意图与目标；2) 已经确定的关键结论、参数、路径、约定；"
    "3) 尚未完成或待办的事项；4) 任何对后续对话有用的重要上下文。\n"
    "保留所有专有名词、文件/目录路径、参数值、技术术语，不要编造新信息。"
)


def split_for_compression(messages: list[dict[str, Any]], recent_turns: int):
    """把消息分为「待压缩的早期」与「保留原文的近期」。"""
    if len(messages) <= recent_turns:
        return [], messages
    recent = messages[-recent_turns:]
    old = messages[:-recent_turns]
    return old, recent


def prepare_messages(
    system_prompt: str,
    conversation_id: str,
    *,
    summarizer: Callable[[str], str] | None = None,
    compress: bool = True,
    recent_turns: int = DEFAULT_RECENT_TURNS,
) -> list[dict[str, str]]:
    """构造传给 LLM 的消息列表（系统提示 + 压缩历史 + 近期原文）。"""
    out = [{"role": "system", "content": system_prompt}]
    if not conversation_id:
        return out

    conv = load_conversation(conversation_id)
    if not conv:
        return out

    raw = get_messages_for_llm(conversation_id)
    if not compress or len(raw) <= recent_turns:
        return out + raw

    old, recent = split_for_compression(raw, recent_turns)
    summary = conv.metadata.get("summary")

    # 仅当压缩范围变化时才重新生成摘要（用消息数做缓存）
    if summary is None or conv.metadata.get("summary_msg_count") != len(old):
        if summarizer is not None:
            old_text = "\n".join(
                f"{m.get('role', '')}: {m.get('content', '')}" for m in old
            )
            try:
                summary = summarizer(old_text)
            except Exception:
                summary = None
        conv.metadata["summary"] = summary
        conv.metadata["summary_msg_count"] = len(old)
        try:
            save_conversation(conv)
        except Exception:
            pass

    if summary:
        out.append({
            "role": "system",
            "content": "## 对话历史压缩摘要（请据此理解前文背景）\n" + summary,
        })
    return out + recent
