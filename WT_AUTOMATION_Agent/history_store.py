# encoding: utf-8
"""会话历史存储管理模块。

提供对话历史的持久化、加载和列表功能。
存储路径: WT_AUTOMATION_Agent/history/
"""
from __future__ import annotations

import json
import os
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------------

HISTORY_DIR = Path(__file__).resolve().parent / "history"
"""会话历史存储根目录。"""

MAX_SESSIONS = 100
"""最多保留的会话数量（超出时删除最旧的）。"""

MAX_MESSAGES_PER_SESSION = 200
"""单会话最大消息数（超出时截断早期消息）。"""


# ---------------------------------------------------------------------------
# 数据模型
# ---------------------------------------------------------------------------

@dataclass
class Conversation:
    """会话数据模型。"""
    id: str
    title: str
    created_at: str
    updated_at: str
    messages: list[dict[str, Any]] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def new(cls, title: str = "") -> "Conversation":
        """创建新会话。"""
        now = datetime.now().isoformat()
        return cls(
            id=str(uuid.uuid4())[:8],
            title=title or f"新会话 {datetime.now().strftime('%m-%d %H:%M')}",
            created_at=now,
            updated_at=now,
            messages=[],
        )

    @classmethod
    def from_dict(cls, data: dict) -> "Conversation":
        """从字典加载。"""
        return cls(
            id=data.get("id", ""),
            title=data.get("title", ""),
            created_at=data.get("created_at", ""),
            updated_at=data.get("updated_at", ""),
            messages=data.get("messages", []),
            metadata=data.get("metadata", {}),
        )

    def to_dict(self) -> dict:
        """序列化为字典。"""
        return {
            "id": self.id,
            "title": self.title,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "messages": self.messages,
            "metadata": self.metadata,
        }


# ---------------------------------------------------------------------------
# 存储管理
# ---------------------------------------------------------------------------

def _ensure_history_dir() -> Path:
    """确保历史目录存在。"""
    path = HISTORY_DIR
    path.mkdir(parents=True, exist_ok=True)
    return path


def _session_path(session_id: str) -> Path:
    """获取会话文件路径。"""
    return _ensure_history_dir() / f"{session_id}.json"


def list_conversations() -> list[dict[str, Any]]:
    """列出所有会话（按更新时间倒序）。"""
    history_dir = _ensure_history_dir()
    sessions = []

    for fp in history_dir.glob("*.json"):
        try:
            with open(fp, "r", encoding="utf-8") as f:
                data = json.load(f)
            sessions.append({
                "id": data.get("id", fp.stem),
                "title": data.get("title", "未命名"),
                "created_at": data.get("created_at", ""),
                "updated_at": data.get("updated_at", ""),
                "message_count": len(data.get("messages", [])),
            })
        except (json.JSONDecodeError, OSError):
            continue

    # 按更新时间倒序
    sessions.sort(key=lambda x: x.get("updated_at", ""), reverse=True)
    return sessions


def load_conversation(session_id: str) -> Conversation | None:
    """加载指定会话。"""
    fp = _session_path(session_id)
    if not fp.exists():
        return None

    try:
        with open(fp, "r", encoding="utf-8") as f:
            data = json.load(f)
        return Conversation.from_dict(data)
    except (json.JSONDecodeError, OSError):
        return None


def save_conversation(conversation: Conversation) -> bool:
    """保存会话到磁盘。"""
    try:
        fp = _session_path(conversation.id)
        conversation.updated_at = datetime.now().isoformat()

        with open(fp, "w", encoding="utf-8") as f:
            json.dump(conversation.to_dict(), f, ensure_ascii=False, indent=2)

        # 清理旧会话
        _cleanup_old_sessions()
        return True
    except OSError:
        return False


def delete_conversation(session_id: str) -> bool:
    """删除指定会话。"""
    fp = _session_path(session_id)
    if fp.exists():
        try:
            fp.unlink()
            return True
        except OSError:
            pass
    return False


def rename_conversation(session_id: str, new_title: str) -> bool:
    """重命名会话。"""
    conv = load_conversation(session_id)
    if not conv:
        return False

    conv.title = new_title
    return save_conversation(conv)


def _cleanup_old_sessions() -> None:
    """清理超出数量限制的旧会话。"""
    sessions = list_conversations()
    if len(sessions) <= MAX_SESSIONS:
        return

    # 删除超出部分的旧会话
    for session in sessions[MAX_SESSIONS:]:
        delete_conversation(session["id"])


# ---------------------------------------------------------------------------
# 便捷函数（供外部导入）
# ---------------------------------------------------------------------------

def create_conversation(title: str = "") -> Conversation:
    """创建新会话（便捷函数）。"""
    conv = Conversation.new(title=title)
    save_conversation(conv)
    return conv


# ---------------------------------------------------------------------------
# 消息管理
# ---------------------------------------------------------------------------

def add_message(
    session_id: str,
    role: str,
    content: str,
    extra: dict | None = None,
) -> bool:
    """向会话添加消息。

    Args:
        session_id: 会话ID
        role: 角色 ('user' 或 'assistant')
        content: 消息内容
        extra: 额外数据（如 steps, reply 等）

    Returns:
        是否成功
    """
    conv = load_conversation(session_id)
    if not conv:
        return False

    message: dict[str, Any] = {
        "role": role,
        "content": content,
        "timestamp": datetime.now().isoformat(),
    }

    if extra:
        message["extra"] = extra

    conv.messages.append(message)

    # 限制消息数量
    if len(conv.messages) > MAX_MESSAGES_PER_SESSION:
        conv.messages = conv.messages[-MAX_MESSAGES_PER_SESSION:]

    # 自动生成标题（从第一条用户消息截取）
    if not conv.title.startswith("新会话") and len(conv.messages) == 1 and role == "user":
        conv.title = content[:30] + ("..." if len(content) > 30 else "")

    return save_conversation(conv)


def get_messages_for_llm(session_id: str) -> list[dict[str, Any]]:
    """获取适合传给 LLM 的消息格式。

    移除内部元数据，保留 role 和 content。
    """
    conv = load_conversation(session_id)
    if not conv:
        return []

    result = []
    for msg in conv.messages:
        result.append({
            "role": msg.get("role", "user"),
            "content": msg.get("content", ""),
        })
    return result


def clear_conversation(session_id: str) -> bool:
    """清空会话消息（保留会话本身）。"""
    conv = load_conversation(session_id)
    if not conv:
        return False

    conv.messages = []
    conv.metadata.pop("summary", None)
    conv.metadata.pop("summary_msg_count", None)
    conv.title = f"新会话 {datetime.now().strftime('%m-%d %H:%M')}"
    return save_conversation(conv)
