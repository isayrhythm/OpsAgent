from __future__ import annotations

from typing import Any


SKILL_HISTORY_LIMIT = 8
SKILL_HISTORY_ITEM_CHARS = 2000


def build_skill_message_with_context(message: str, history: list[Any] | None) -> str:
    """构造传给 skill 执行层的用户消息。

    router 已经能看到 `history[-8:]`。之前执行层只拿当前请求和上一轮
    用户/助手消息，所以会出现“路由选对了 query_gene_info，但真正执行时
    丢了更早基因 ID”的问题。这里统一给注册工具和生成代码工具同一段最近
    上下文，并限制每条长度，避免 prompt 无限膨胀。
    """
    recent_history = _recent_history(history or [])
    focus = _recent_focus(recent_history)
    if not recent_history:
        return message

    lines = [
        f"当前用户请求：{message}",
        f"上一轮用户请求：{focus['last_user_message']}",
        f"上一轮助手回复：{focus['last_assistant_message']}",
        f"最近上下文（最多 {SKILL_HISTORY_LIMIT} 条，旧到新）：",
    ]
    for item in recent_history:
        role = str(getattr(item, "role", "") or "unknown")
        content = _bounded_content(str(getattr(item, "content", "") or ""))
        if content:
            lines.append(f"- {role}: {content}")
    return "\n".join(lines)


def _recent_history(history: list[Any]) -> list[Any]:
    return [item for item in history[-SKILL_HISTORY_LIMIT:] if str(getattr(item, "content", "") or "").strip()]


def _recent_focus(history: list[Any]) -> dict[str, str]:
    focus = {"last_user_message": "", "last_assistant_message": ""}
    for item in reversed(history):
        content = _bounded_content(str(getattr(item, "content", "") or ""))
        role = str(getattr(item, "role", "") or "")
        if not content:
            continue
        if role == "user" and not focus["last_user_message"]:
            focus["last_user_message"] = content
        elif role in {"assistant", "agent"} and not focus["last_assistant_message"]:
            focus["last_assistant_message"] = content
        if focus["last_user_message"] and focus["last_assistant_message"]:
            break
    return focus


def _bounded_content(content: str) -> str:
    value = " ".join(str(content or "").split())
    if len(value) <= SKILL_HISTORY_ITEM_CHARS:
        return value
    return value[: SKILL_HISTORY_ITEM_CHARS - 15].rstrip() + "... <truncated>"
