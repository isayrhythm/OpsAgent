from __future__ import annotations

from typing import Any

from backend.app.schemas import TaskEvent


MAX_SUMMARY_CHARS = 240
INTERNAL_AGENTS = {"Research Planner", "Research Synthesizer"}


def build_tool_trace(events: list[TaskEvent], result: dict[str, Any] | None = None) -> dict[str, Any]:
    """把本轮工具调用压成轻量轨迹。

    这里不保存完整工具结果，只保存“调用了什么、成功失败、关键输入输出摘要”。
    这样下一轮模型知道过程，但不会把搜索结果、命令 stdout、大表格塞爆上下文。
    """
    entries: list[dict[str, Any]] = []
    by_name: dict[str, dict[str, Any]] = {}

    for event in events:
        if event.type != "progress":
            continue
        data = event.data if isinstance(event.data, dict) else {}
        name = _tool_name(event.status, data)
        if not name or name in INTERNAL_AGENTS:
            continue

        entry = by_name.get(name)
        if entry is None:
            entry = {
                "name": name,
                "type": _tool_type(name, data),
                "status": "running",
                "attempts": 0,
                "input_summary": "",
                "output_summary": "",
            }
            by_name[name] = entry
            entries.append(entry)

        _update_status(entry, event.status, data)
        input_summary = _input_summary(event.status, data)
        if input_summary:
            entry["input_summary"] = input_summary
        output_summary = _output_summary(data)
        if output_summary:
            entry["output_summary"] = output_summary

    _merge_result_summaries(entries, result or {})
    return {"tools": entries}


def format_tool_trace_context(trace: dict[str, Any]) -> str:
    tools = trace.get("tools") if isinstance(trace, dict) else []
    if not isinstance(tools, list) or not tools:
        return ""

    lines = ["上一轮工具调用摘要："]
    for item in tools[:12]:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        if not name:
            continue
        tool_type = str(item.get("type") or "tool")
        status = str(item.get("status") or "unknown")
        attempts = int(item.get("attempts") or 0)
        details = [str(item.get("input_summary") or "").strip(), str(item.get("output_summary") or "").strip()]
        details = [part for part in details if part]
        detail_text = f"；{'；'.join(details)}" if details else ""
        attempt_text = f"；attempts={attempts}" if attempts > 1 else ""
        lines.append(f"- {name} [{tool_type}]: {status}{attempt_text}{detail_text}")
    return "\n".join(lines)


def append_tool_trace_to_context(answer: str, trace_context: str) -> str:
    if not trace_context:
        return answer
    return f"{answer}\n\n{trace_context}"


def _tool_name(status: str, data: dict[str, Any]) -> str:
    agent = str(data.get("agent") or "").strip()
    if agent:
        return agent
    if "Shell Command" in status:
        return "Shell Command"
    if "Web Search" in status:
        return "Web Search"
    if "File Context" in status or "File Inspector" in status:
        return "File Inspector"
    for prefix in ("Running Skill:", "Retrying Skill:", "Skill Completed:"):
        if status.startswith(prefix):
            return status.removeprefix(prefix).strip()
    return ""


def _tool_type(name: str, data: dict[str, Any]) -> str:
    if name in {"Web Search", "Tavily Search", "Quark Search", "Search Query Rewriter"}:
        return "search"
    if name == "Shell Command":
        return "command"
    if name in {"File Inspector", "File Transformer"} or data.get("target_skill"):
        return "file"
    return "skill"


def _update_status(entry: dict[str, Any], status: str, data: dict[str, Any]) -> None:
    state = str(data.get("agent_state") or "").lower()
    if state == "running" or status.startswith(("Running", "Retrying", "Repairing")):
        entry["attempts"] = int(entry.get("attempts") or 0) + 1
        entry["status"] = "running"
    elif state in {"done", "completed"} or "Completed" in status or "Ready" in status:
        entry["status"] = "completed"
    elif state in {"failed", "error"} or "Failed" in status:
        entry["status"] = "failed"


def _input_summary(status: str, data: dict[str, Any]) -> str:
    parts: list[str] = []
    queries = data.get("queries")
    if isinstance(queries, list) and queries:
        parts.append("queries: " + ", ".join(str(item) for item in queries[:3]))
    command = data.get("command")
    if command:
        parts.append(f"command: {command}")
    files = data.get("files")
    if isinstance(files, list) and files:
        parts.append("files: " + ", ".join(str(item) for item in files[:4]))
    target_skill = data.get("target_skill")
    if target_skill:
        parts.append(f"target skill: {target_skill}")
    reason = data.get("reason")
    if reason:
        parts.append(f"reason: {reason}")
    return _bounded("; ".join(parts))


def _output_summary(data: dict[str, Any]) -> str:
    attempts = data.get("attempts")
    if attempts:
        return _bounded(f"attempts: {attempts}")
    return ""


def _merge_result_summaries(entries: list[dict[str, Any]], result: dict[str, Any]) -> None:
    skill_outputs = result.get("skill_outputs") if isinstance(result.get("skill_outputs"), list) else []
    skill_output = result.get("skill_output") if isinstance(result.get("skill_output"), dict) else None
    if skill_output:
        skill_outputs = [skill_output, *skill_outputs]

    for output in skill_outputs:
        if not isinstance(output, dict):
            continue
        name = str(output.get("skill_name") or "").strip()
        if not name:
            continue
        entry = _entry_for(entries, name)
        entry["type"] = "skill"
        entry["status"] = "completed"
        entry["output_summary"] = _summarize_skill_output(output)

    search = result.get("search") if isinstance(result.get("search"), dict) else {}
    sources = result.get("web_sources") or search.get("sources") or []
    if isinstance(sources, list) and sources:
        entry = _entry_for(entries, "Web Search")
        entry["type"] = "search"
        entry["status"] = "completed"
        entry["output_summary"] = f"sources: {len(sources)}"


def _entry_for(entries: list[dict[str, Any]], name: str) -> dict[str, Any]:
    for entry in entries:
        if entry.get("name") == name:
            return entry
    entry = {"name": name, "type": "tool", "status": "completed", "attempts": 1, "input_summary": "", "output_summary": ""}
    entries.append(entry)
    return entry


def _summarize_skill_output(output: dict[str, Any]) -> str:
    result = output.get("result")
    if isinstance(result, dict):
        for key in ("matches", "records", "genes", "results", "items"):
            value = result.get(key)
            if isinstance(value, list):
                ids = _first_ids(value)
                suffix = f"; ids: {', '.join(ids)}" if ids else ""
                return _bounded(f"{key}: {len(value)}{suffix}")
        status = result.get("status")
        if status:
            return _bounded(f"status: {status}")
    if isinstance(result, list):
        ids = _first_ids(result)
        suffix = f"; ids: {', '.join(ids)}" if ids else ""
        return _bounded(f"items: {len(result)}{suffix}")
    return ""


def _first_ids(items: list[Any]) -> list[str]:
    ids: list[str] = []
    for item in items[:5]:
        if not isinstance(item, dict):
            continue
        for key in ("gene_id", "gene", "id", "locus", "name"):
            value = item.get(key)
            if value:
                ids.append(str(value))
                break
    return ids


def _bounded(value: str) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= MAX_SUMMARY_CHARS:
        return text
    return text[: MAX_SUMMARY_CHARS - 15].rstrip() + "... <truncated>"
