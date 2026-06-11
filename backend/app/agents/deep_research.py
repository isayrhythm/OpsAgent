from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Literal, TypedDict

from langgraph.graph import END, StateGraph

from backend.app.llm.prompts import (
    DEEP_RESEARCH_EVALUATOR_SYSTEM_PROMPT,
    DEEP_RESEARCH_INTENT_SYSTEM_PROMPT,
    DEEP_RESEARCH_PLANNER_SYSTEM_PROMPT,
    DEEP_RESEARCH_SYNTHESIZER_SYSTEM_PROMPT,
    DEEP_RESEARCH_TASK_SUMMARY_SYSTEM_PROMPT,
)
from backend.app.llm.calls import chat_json, chat_text, complete_text
from backend.app.schemas import ChatHistoryMessage
from backend.app.services.result_evaluator import compact_value
from backend.app.services.deepseek_client import DeepSeekClient
from backend.app.services.skill_loader import SkillSpec
from backend.app.services.skill_runtime import SkillExecutionContext, execute_registered_skill
from backend.app.tools.web_search import format_web_search_context, search_web_queries, web_search_sources
from backend.app.tools.web_search_planner import plan_web_search


Emit = Callable[[str, int, str, Any | None], Awaitable[None]]
ResearchTaskStatus = Literal["pending", "running", "completed", "failed", "skipped"]
SEARCH_TOOL_NAMES = {"Search Query Rewriter", "Tavily Search", "Quark Search"}


@dataclass
class ResearchTask:
    id: str
    title: str
    question: str
    purpose: str = ""
    dependencies: list[str] = field(default_factory=list)
    tools: list[str] = field(default_factory=list)
    tool_details: list[dict[str, Any]] = field(default_factory=list)
    status: ResearchTaskStatus = "pending"
    summary: str = ""
    evidence: list[dict[str, Any]] = field(default_factory=list)
    skill_outputs: list[dict[str, Any]] = field(default_factory=list)
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "step": self.id,
            "title": self.title,
            "question": self.question,
            "purpose": self.purpose,
            "dependencies": self.dependencies,
            "tools": self.tools,
            "tool_details": self.tool_details,
            "status": self.status,
            "summary": self.summary,
            "evidence_count": len(self.evidence),
            "skill_output_count": len(self.skill_outputs),
            "error": self.error,
        }


class ResearchState(TypedDict, total=False):
    message: str
    history: list[ChatHistoryMessage]
    providers: list[str]
    skills: list[SkillSpec]
    intent: dict[str, Any]
    plan: dict[str, Any]
    tasks: list[ResearchTask]
    completed_tasks: list[dict[str, Any]]
    evaluations: list[dict[str, Any]]
    repair_attempts: int
    continue_research: bool
    answer: str
    sources: list[dict[str, Any]]


class ResearchPlanner:
    def __init__(self, llm: DeepSeekClient) -> None:
        self.llm = llm

    async def classify_intent(self, message: str, history: list[ChatHistoryMessage]) -> dict[str, Any]:
        fallback = classify_deep_research_intent(message)
        if not self.llm.available:
            return fallback
        payload = {
            "message": message,
            "history": [{"role": item.role, "content": item.content} for item in history[-8:]],
            "output_schema": {
                "deep_research": "boolean",
                "reason": "short reason",
                "research_goal": "what should be researched",
            },
        }
        try:
            data = await chat_json(
                self.llm,
                [
                    {"role": "system", "content": DEEP_RESEARCH_INTENT_SYSTEM_PROMPT},
                    {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
                ],
                model=self.llm.settings.router_model,
                temperature=0,
                max_tokens=500,
            )
        except Exception:
            return fallback
        return {
            "deep_research": bool(data.get("deep_research")) or fallback["deep_research"],
            "reason": str(data.get("reason") or fallback["reason"]),
            "research_goal": str(data.get("research_goal") or message).strip(),
        }

    async def plan_research(
        self,
        message: str,
        history: list[ChatHistoryMessage],
        providers: list[str],
        skills: list[SkillSpec],
    ) -> dict[str, Any]:
        tool_catalog = _research_tool_catalog(providers, skills)
        fallback = self._fallback_plan(message, providers, tool_catalog)
        if not self.llm.available:
            return fallback
        payload = {
            "user_message": message,
            "history": [{"role": item.role, "content": item.content} for item in history[-8:]],
            "providers": providers,
            "available_tools": tool_catalog,
            "requirements": [
                "Create 3-6 DAG tasks.",
                "Each task must have id, title, question, purpose, dependencies.",
                "Each task must choose tools from available_tools by exact tool name.",
                "Use skill tools when their description or trigger matches a task.",
                "Use search tools for fresh/public evidence gathering and cross-checking.",
                "Use dependencies only when one task needs another result.",
                "Prefer independent tasks when they can run in parallel.",
                "Return planned tool choices only; do not claim tools were already executed.",
            ],
        }
        try:
            data = await chat_json(
                self.llm,
                [
                    {"role": "system", "content": DEEP_RESEARCH_PLANNER_SYSTEM_PROMPT},
                    {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
                ],
                model=self.llm.settings.router_model,
                temperature=0,
                max_tokens=1200,
            )
        except Exception:
            return fallback
        tasks = data.get("tasks") if isinstance(data.get("tasks"), list) else []
        return {
            "summary": str(data.get("summary") or message),
            "tools": tool_catalog,
            "tasks": tasks,
        } if tasks else fallback

    def validate_plan(
        self,
        plan: dict[str, Any],
        message: str,
        providers: list[str] | None = None,
        tools: list[dict[str, Any]] | None = None,
    ) -> tuple[dict[str, Any], list[ResearchTask]]:
        raw_tasks = plan.get("tasks") if isinstance(plan.get("tasks"), list) else []
        tasks: list[ResearchTask] = []
        seen: set[str] = set()
        tool_catalog = tools or (plan.get("tools") if isinstance(plan.get("tools"), list) else [])
        if not tool_catalog:
            tool_catalog = _research_tool_catalog(providers or [], [])
        allowed_tools = {str(item.get("name") or item.get("label") or "").strip() for item in tool_catalog if isinstance(item, dict)}
        for index, item in enumerate(raw_tasks[:6], start=1):
            if not isinstance(item, dict):
                continue
            raw_id = str(item.get("id") or index).strip()
            task_id = _slug_step_id(raw_id, index)
            if task_id in seen:
                task_id = f"T{index}"
            seen.add(task_id)
            raw_tools = [str(tool).strip() for tool in item.get("tools", []) if str(tool).strip()]
            selected_tools = _bounded_task_tools(
                raw_tools,
                item,
                allowed_tools=allowed_tools,
                catalog=tool_catalog,
            )
            tasks.append(
                ResearchTask(
                    id=task_id,
                    title=str(item.get("title") or f"Research step {index}").strip(),
                    question=str(item.get("question") or message).strip(),
                    purpose=str(item.get("purpose") or "").strip(),
                    dependencies=[str(dep).strip() for dep in item.get("dependencies", []) if str(dep).strip()],
                    tools=selected_tools,
                )
            )
        if not tasks:
            return self._fallback_plan(message, providers or [], tool_catalog), self._fallback_tasks(message, providers or [], tool_catalog)
        _ensure_required_research_tasks(tasks, message)
        valid_ids = {task.id for task in tasks}
        for task in tasks:
            task.dependencies = [dep for dep in task.dependencies if dep in valid_ids and dep != task.id]
        _apply_research_dependencies(tasks)
        for task in tasks:
            task.tool_details = _tool_details(task.tools, tool_catalog)
        return {**plan, "tools": tool_catalog, "tasks": [task.to_dict() for task in tasks]}, tasks

    def _fallback_plan(
        self,
        message: str,
        providers: list[str] | None = None,
        tools: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        tool_catalog = tools or _research_tool_catalog(providers or [], [])
        return {
            "summary": f"Research plan for: {message}",
            "tools": tool_catalog,
            "tasks": [task.to_dict() for task in self._fallback_tasks(message, providers or [], tool_catalog)],
        }

    def _fallback_tasks(
        self,
        message: str,
        providers: list[str] | None = None,
        tool_catalog: list[dict[str, Any]] | None = None,
    ) -> list[ResearchTask]:
        tools = [str(item.get("name") or item.get("label") or "").strip() for item in (tool_catalog or _research_tool_catalog(providers or [], []))]
        details = _tool_details(tools, tool_catalog or _research_tool_catalog(providers or [], []))
        return [
            ResearchTask("T1", "Clarify the research question", message, "Identify the exact answer scope.", tools=tools, tool_details=details),
            ResearchTask("T2", "Gather current evidence", message, "Search and collect relevant sources.", tools=tools, tool_details=details),
            ResearchTask("T3", "Cross-check and synthesize", message, "Compare findings and prepare the answer.", ["T1", "T2"], tools=tools, tool_details=details),
        ]


class ResearchExecutor:
    def __init__(self, llm: DeepSeekClient) -> None:
        self.llm = llm

    async def execute_dag(
        self,
        tasks: list[ResearchTask],
        history: list[ChatHistoryMessage],
        providers: list[str],
        skills: list[SkillSpec],
        emit_step: Callable[[ResearchTask], Awaitable[None]],
    ) -> list[ResearchTask]:
        completed: set[str] = {task.id for task in tasks if task.status == "completed"}
        remaining = {task.id: task for task in tasks if task.status != "completed"}
        while remaining:
            ready = [
                task
                for task in remaining.values()
                if all(dep in completed for dep in task.dependencies)
            ]
            if not ready:
                ready = [next(iter(remaining.values()))]
            await asyncio.gather(
                *(
                    self._execute_task(
                        task,
                        tasks,
                        history,
                        providers,
                        skills,
                        emit_step,
                    )
                    for task in ready
                )
            )
            for task in ready:
                completed.add(task.id)
                remaining.pop(task.id, None)
        return tasks

    async def _execute_task(
        self,
        task: ResearchTask,
        all_tasks: list[ResearchTask],
        history: list[ChatHistoryMessage],
        providers: list[str],
        skills: list[SkillSpec],
        emit_step: Callable[[ResearchTask], Awaitable[None]],
    ) -> None:
        task.status = "running"
        await emit_step(task)
        try:
            if _task_uses_skill(task):
                await self._execute_skill_task(task, all_tasks, history, skills)
                task.status = "completed" if task.skill_outputs else "skipped"
                await emit_step(task)
                return
            if not _task_uses_search(task):
                task.summary = "This step will synthesize outputs from its dependencies."
                task.status = "completed"
                await emit_step(task)
                return
            search_plan = await plan_web_search(
                task.question,
                history=history,
                mode="force",
                providers=providers,
                llm=self.llm,
            )
            search_result = await search_web_queries(
                [query.to_dict() for query in search_plan.queries],
                history=history,
                providers=providers,
            )
            task.evidence = web_search_sources(search_result)
            context = format_web_search_context(search_result)
            task.summary = await self._summarize_task(task, context)
            task.status = "completed"
        except Exception as exc:
            task.status = "failed"
            task.error = str(exc)
            task.summary = f"Step failed: {exc}"
        await emit_step(task)

    async def _execute_skill_task(
        self,
        task: ResearchTask,
        all_tasks: list[ResearchTask],
        history: list[ChatHistoryMessage],
        skills: list[SkillSpec],
    ) -> None:
        skills_by_name = {skill.name: skill for skill in skills}
        message = _task_execution_message(task, all_tasks)
        outputs: list[dict[str, Any]] = []
        errors: list[str] = []
        for tool_name in [tool for tool in task.tools if tool not in SEARCH_TOOL_NAMES]:
            skill = skills_by_name.get(tool_name)
            if skill is None:
                errors.append(f"Skill is not available: {tool_name}")
                continue
            if not skill.executor:
                errors.append(f"Skill is not executable in deep research yet: {tool_name}")
                continue
            try:
                output = await execute_registered_skill(
                    skill,
                    SkillExecutionContext(
                        message=message,
                        history=history,
                        attachments=[],
                        data_profiles=[],
                        llm=self.llm,
                    ),
                )
            except Exception as exc:
                errors.append(f"{tool_name}: {exc}")
                continue
            outputs.append({"skill_name": tool_name, "output": output})
        task.skill_outputs = outputs
        if outputs:
            task.summary = await self._summarize_skill_outputs(task, outputs)
        elif errors:
            task.summary = "Skill execution failed or unavailable: " + "; ".join(errors)
            task.error = task.summary
        else:
            task.summary = "No executable skill was selected for this step."

    async def _summarize_task(self, task: ResearchTask, context: str) -> str:
        if not self.llm.available:
            return context[:900] if context else "No evidence returned."
        payload = {
            "task": task.to_dict(),
            "evidence_context": context,
            "instruction": "Summarize only evidence relevant to this task. Keep citations like [1] when present.",
        }
        try:
            response = await chat_text(
                self.llm,
                [
                    {"role": "system", "content": DEEP_RESEARCH_TASK_SUMMARY_SYSTEM_PROMPT},
                    {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
                ],
                model=self.llm.settings.answer_model,
                temperature=0.2,
                max_tokens=700,
            )
        except Exception:
            return context[:900] if context else "No evidence returned."
        return response.strip()

    async def _summarize_skill_outputs(self, task: ResearchTask, outputs: list[dict[str, Any]]) -> str:
        compact_outputs = compact_value(outputs)
        if not self.llm.available:
            return json.dumps(compact_outputs, ensure_ascii=False)[:1200]
        payload = {
            "task": task.to_dict(),
            "skill_outputs": compact_outputs,
            "instruction": "Summarize only facts returned by the skill outputs. Preserve gene IDs and source fields.",
        }
        try:
            response = await chat_text(
                self.llm,
                [
                    {"role": "system", "content": DEEP_RESEARCH_TASK_SUMMARY_SYSTEM_PROMPT},
                    {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
                ],
                model=self.llm.settings.answer_model,
                temperature=0.2,
                max_tokens=700,
            )
        except Exception:
            return json.dumps(compact_outputs, ensure_ascii=False)[:1200]
        return response.strip()


class ResearchEvaluator:
    def __init__(self, llm: DeepSeekClient) -> None:
        self.llm = llm

    async def evaluate_steps(self, tasks: list[ResearchTask], question: str) -> dict[str, Any]:
        missing = [task.id for task in tasks if task.status != "completed" or not task.summary.strip()]
        enough = not missing and any(task.evidence or task.skill_outputs for task in tasks)
        if not self.llm.available:
            return {"sufficient": enough, "missing": missing, "repair_tasks": []}
        payload = {
            "question": question,
            "steps": [task.to_dict() for task in tasks],
            "summaries": [{"id": task.id, "summary": task.summary} for task in tasks],
        }
        try:
            data = await chat_json(
                self.llm,
                [
                    {"role": "system", "content": DEEP_RESEARCH_EVALUATOR_SYSTEM_PROMPT},
                    {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
                ],
                model=self.llm.settings.router_model,
                temperature=0,
                max_tokens=700,
            )
        except Exception:
            return {"sufficient": enough, "missing": missing, "repair_tasks": []}
        return {
            "sufficient": bool(data.get("sufficient")) or enough,
            "missing": data.get("missing") if isinstance(data.get("missing"), list) else missing,
            "repair_tasks": data.get("repair_tasks") if isinstance(data.get("repair_tasks"), list) else [],
        }


class ResearchSynthesizer:
    def __init__(self, llm: DeepSeekClient) -> None:
        self.llm = llm

    async def synthesize_answer(
        self,
        question: str,
        plan: dict[str, Any],
        tasks: list[ResearchTask],
        evaluations: list[dict[str, Any]],
        emit_delta: Callable[[str], Awaitable[None]] | None = None,
    ) -> str:
        payload = {
            "user_message": question,
            "research_plan": plan,
            "research_steps": [
                {
                    **task.to_dict(),
                    "summary": task.summary,
                    "evidence": task.evidence[:8],
                    "skill_outputs": compact_value(task.skill_outputs),
                }
                for task in tasks
            ],
            "evaluations": evaluations,
            "answer_requirements": [
                "Answer directly.",
                "Use only evidence from research_steps.",
                "Cite web evidence with source indexes like [1].",
                "Call out uncertainty or missing evidence explicitly.",
            ],
        }
        if not self.llm.available:
            answer = "\n\n".join(f"### {task.title}\n{task.summary}" for task in tasks if task.summary)
            if emit_delta and answer:
                await emit_delta(answer)
            return answer
        return await complete_text(
            self.llm,
            [
                {"role": "system", "content": DEEP_RESEARCH_SYNTHESIZER_SYSTEM_PROMPT},
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
            ],
            model=self.llm.settings.answer_model,
            temperature=0.2,
            max_tokens=2200,
            emit_delta=emit_delta,
        )


def classify_deep_research_intent(message: str) -> dict[str, Any]:
    text = message.lower()
    keywords = [
        "深度研究",
        "深入研究",
        "系统研究",
        "调研",
        "综述",
        "全面分析",
        "research plan",
        "deep research",
        "literature review",
    ]
    deep = any(keyword in text for keyword in keywords) or len(message) > 120 and any(mark in message for mark in "？?")
    return {
        "deep_research": deep,
        "reason": "User asked for multi-step research." if deep else "No deep research intent detected.",
        "research_goal": message,
    }


def should_route_deep_research(message: str) -> bool:
    return bool(classify_deep_research_intent(message).get("deep_research"))


def build_research_graph(llm: DeepSeekClient, emit: Emit):
    planner = ResearchPlanner(llm)
    executor = ResearchExecutor(llm)
    evaluator = ResearchEvaluator(llm)
    synthesizer = ResearchSynthesizer(llm)
    graph = StateGraph(ResearchState)

    async def classify_intent(state: ResearchState) -> ResearchState:
        intent = await planner.classify_intent(state["message"], state.get("history", []))
        return {"intent": intent, "repair_attempts": 0}

    async def plan_research(state: ResearchState) -> ResearchState:
        await emit("progress", 4, "正在制定研究计划", None)
        plan = await planner.plan_research(
            state["message"],
            state.get("history", []),
            state.get("providers", []),
            state.get("skills", []),
        )
        return {"plan": plan}

    async def validate_plan(state: ResearchState) -> ResearchState:
        plan, tasks = planner.validate_plan(
            state.get("plan", {}),
            state["message"],
            state.get("providers", []),
            state.get("plan", {}).get("tools") if isinstance(state.get("plan", {}).get("tools"), list) else None,
        )
        await emit(
            "progress",
            4,
            "研究计划已生成",
            {
                "research_plan": {
                    "summary": plan.get("summary"),
                    "tools": plan.get("tools") or [],
                    "steps": [task.to_dict() for task in tasks],
                }
            },
        )
        return {"plan": plan, "tasks": tasks}

    async def execute_dag(state: ResearchState) -> ResearchState:
        async def emit_step(task: ResearchTask) -> None:
            await emit(
                "progress",
                5,
                f"{task.title}: {task.status}",
                {"research_step": task.to_dict()},
            )

        tasks = await executor.execute_dag(
            state.get("tasks", []),
            state.get("history", []),
            state.get("providers", []),
            state.get("skills", []),
            emit_step,
        )
        sources = _merge_sources(tasks)
        if sources:
            await emit("source_delta", 6, "已获取研究来源", {"sources": sources})
        return {"tasks": tasks, "completed_tasks": [task.to_dict() for task in tasks], "sources": sources}

    async def evaluate_steps(state: ResearchState) -> ResearchState:
        evaluation = await evaluator.evaluate_steps(state.get("tasks", []), state["message"])
        evaluations = [*state.get("evaluations", []), evaluation]
        return {"evaluations": evaluations}

    async def repair_or_continue(state: ResearchState) -> ResearchState:
        latest = (state.get("evaluations") or [{}])[-1]
        attempts = int(state.get("repair_attempts") or 0)
        if latest.get("sufficient") or attempts >= 1:
            return {"continue_research": False}
        repair_tasks = latest.get("repair_tasks") if isinstance(latest.get("repair_tasks"), list) else []
        if not repair_tasks:
            repair_tasks = [
                {
                    "id": "R1",
                    "title": "Repair missing evidence",
                    "question": f"{state['message']} missing evidence: {latest.get('missing')}",
                    "purpose": "Fill evidence gaps found by evaluation.",
                    "dependencies": [task.id for task in state.get("tasks", []) if task.status == "completed"][-2:],
                }
            ]
        _, tasks = planner.validate_plan({"summary": "Repair plan", "tasks": repair_tasks}, state["message"], state.get("providers", []))
        return {"tasks": [*state.get("tasks", []), *tasks], "repair_attempts": attempts + 1, "continue_research": True}

    async def synthesize_answer(state: ResearchState) -> ResearchState:
        await emit("progress", 7, "正在综合研究结论", None)

        async def emit_delta(delta: str) -> None:
            await emit("answer_delta", 7, "输出中", {"delta": delta})

        answer = await synthesizer.synthesize_answer(
            state["message"],
            state.get("plan", {}),
            state.get("tasks", []),
            state.get("evaluations", []),
            emit_delta=emit_delta,
        )
        return {"answer": answer}

    graph.add_node("classify_intent", classify_intent)
    graph.add_node("plan_research", plan_research)
    graph.add_node("validate_plan", validate_plan)
    graph.add_node("execute_dag", execute_dag)
    graph.add_node("evaluate_steps", evaluate_steps)
    graph.add_node("repair_or_continue", repair_or_continue)
    graph.add_node("synthesize_answer", synthesize_answer)
    graph.set_entry_point("classify_intent")
    graph.add_edge("classify_intent", "plan_research")
    graph.add_edge("plan_research", "validate_plan")
    graph.add_edge("validate_plan", "execute_dag")
    graph.add_edge("execute_dag", "evaluate_steps")
    graph.add_edge("evaluate_steps", "repair_or_continue")
    graph.add_conditional_edges(
        "repair_or_continue",
        lambda state: "execute_dag" if state.get("continue_research") else "synthesize_answer",
        {"execute_dag": "execute_dag", "synthesize_answer": "synthesize_answer"},
    )
    graph.add_edge("synthesize_answer", END)
    return graph.compile()


def _slug_step_id(value: str, index: int) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_-]+", "", value)[:16]
    return cleaned or f"T{index}"


def _merge_sources(tasks: list[ResearchTask]) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    seen: set[str] = set()
    for task in tasks:
        for source in task.evidence:
            url = str(source.get("url") or "")
            if not url or url in seen:
                continue
            seen.add(url)
            merged.append({**source, "index": len(merged) + 1})
    return merged


def _task_execution_message(task: ResearchTask, all_tasks: list[ResearchTask]) -> str:
    dep_tasks = [item for item in all_tasks if item.id in set(task.dependencies)]
    if not dep_tasks:
        return task.question
    sections = [
        f"Current research step: {task.title}",
        f"Question: {task.question}",
    ]
    if task.purpose:
        sections.append(f"Purpose: {task.purpose}")
    sections.append("Dependency outputs:")
    for dep in dep_tasks:
        gene_ids = _extract_gene_ids(dep.skill_outputs)
        sections.append(f"- {dep.id} {dep.title}")
        if dep.summary:
            sections.append(f"  summary: {dep.summary[:1600]}")
        if gene_ids:
            sections.append(f"  candidate_gene_ids: {', '.join(gene_ids[:30])}")
        if dep.skill_outputs:
            sections.append(
                "  structured_result: "
                + json.dumps(compact_value(dep.skill_outputs), ensure_ascii=False)[:3000]
            )
    return "\n".join(sections)


def _extract_gene_ids(value: Any) -> list[str]:
    ids: list[str] = []

    def visit(item: Any) -> None:
        if isinstance(item, dict):
            for key, child in item.items():
                lowered = str(key).lower()
                if lowered in {"gene_id", "canonical_id", "query_id"} and isinstance(child, str):
                    if _looks_like_gene_id(child):
                        ids.append(child)
                    continue
                visit(child)
        elif isinstance(item, list):
            for child in item:
                visit(child)

    visit(value)
    return _dedupe_tool_names(ids)


def _looks_like_gene_id(value: str) -> bool:
    text = str(value or "").strip()
    return bool(
        re.search(
            r"^(AGIS_Os\d+g\d+|LOC_Os\d+g\d+|Os\d+g\d+|AT[1-5CM]G\d+|Zm\d+[A-Za-z0-9_.-]*|Glyma\.\d+G\d+|GmW82\.\d+G\d+)",
            text,
            re.I,
        )
    )


def _research_tool_catalog(providers: list[str], skills: list[SkillSpec]) -> list[dict[str, Any]]:
    catalog: list[dict[str, Any]] = [
        {
            "name": "Search Query Rewriter",
            "type": "search",
            "description": "Rewrite the user's research question into focused search queries.",
            "trigger": "Use before public web evidence gathering.",
        }
    ]
    provider_names = [str(provider).strip().lower() for provider in providers if str(provider).strip()] or ["tavily", "quark"]
    provider_tools = {
        "tavily": {
            "name": "Tavily Search",
            "type": "search",
            "description": "Search public web sources, especially English or keyword-style queries.",
            "trigger": "Use for current public evidence, news, papers, docs, and cross-checking.",
        },
        "quark": {
            "name": "Quark Search",
            "type": "search",
            "description": "Search public web sources with strong Chinese query support.",
            "trigger": "Use for Chinese questions, Chinese sources, local context, and cross-checking.",
        },
        "aliyun": {
            "name": "Quark Search",
            "type": "search",
            "description": "Search public web sources with strong Chinese query support.",
            "trigger": "Use for Chinese questions, Chinese sources, local context, and cross-checking.",
        },
        "opensearch": {
            "name": "Quark Search",
            "type": "search",
            "description": "Search public web sources with strong Chinese query support.",
            "trigger": "Use for Chinese questions, Chinese sources, local context, and cross-checking.",
        },
    }
    for provider in provider_names:
        tool = provider_tools.get(provider)
        if tool and tool["name"] not in [item["name"] for item in catalog]:
            catalog.append(tool)

    for skill in skills:
        catalog.append(
            {
                "name": skill.name,
                "type": "skill",
                "description": skill.description,
                "trigger": skill.trigger,
                "execution_mode": skill.execution_mode,
            }
        )
    return catalog


def _tool_details(names: list[str], catalog: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_name = {str(item.get("name") or ""): item for item in catalog if isinstance(item, dict)}
    details: list[dict[str, Any]] = []
    for name in names:
        item = by_name.get(name)
        if item is None:
            details.append({"name": name, "type": "unknown", "description": "", "trigger": ""})
            continue
        details.append(
            {
                "name": str(item.get("name") or name),
                "type": str(item.get("type") or ""),
                "description": str(item.get("description") or ""),
                "trigger": str(item.get("trigger") or ""),
            }
        )
    return details


def _bounded_task_tools(
    names: list[str],
    task: dict[str, Any],
    *,
    allowed_tools: set[str],
    catalog: list[dict[str, Any]],
) -> list[str]:
    selected = _dedupe_tool_names([name for name in names if name in allowed_tools])
    task_text = " ".join(
        str(task.get(key) or "")
        for key in ("title", "question", "purpose")
    ).lower()
    selected = [name for name in selected if _tool_allowed_for_task(name, task_text)]
    if _is_synthesis_task(task_text):
        return []
    if not selected:
        return _infer_task_tools(task_text, catalog)
    if len(selected) <= 4:
        return selected

    by_name = {str(item.get("name") or ""): item for item in catalog}
    skill_tools = [name for name in selected if by_name.get(name, {}).get("type") == "skill"]
    search_tools = [name for name in selected if by_name.get(name, {}).get("type") == "search"]
    if _looks_like_search_task(task_text):
        return _dedupe_tool_names(search_tools)[:3]
    if skill_tools:
        return _dedupe_tool_names(skill_tools)[:2]
    return selected[:3]


def _apply_research_dependencies(tasks: list[ResearchTask]) -> None:
    for index, task in enumerate(tasks):
        prior = tasks[:index]
        if "query_gene_info" in task.tools:
            for candidate in prior:
                if any(tool in candidate.tools for tool in ("trait2gene_query", "rice_trait_records_query")):
                    _append_dependency(task, candidate.id)
        if "gene_phenotype_prediction" in task.tools:
            for candidate in reversed(prior):
                if any(tool in candidate.tools for tool in ("query_gene_info", "trait2gene_query")):
                    _append_dependency(task, candidate.id)
                    break
        task_text = " ".join([task.title, task.question, task.purpose]).lower()
        if _is_synthesis_task(task_text) and not task.dependencies:
            task.dependencies = [candidate.id for candidate in prior]


def _ensure_required_research_tasks(tasks: list[ResearchTask], message: str) -> None:
    text = message.lower()
    if _message_needs_trait2gene(text) and not any("trait2gene_query" in task.tools for task in tasks):
        tasks.insert(
            0,
            ResearchTask(
                id=_next_task_id(tasks, "T_trait"),
                title="本地数据库查询：性状相关基因",
                question=message,
                purpose="Use the local trait2gene database to collect candidate genes for the requested trait.",
                tools=["trait2gene_query"],
            ),
        )
    if _message_needs_gene_info(text) and not any("query_gene_info" in task.tools for task in tasks):
        trait_task = next((task for task in tasks if "trait2gene_query" in task.tools), None)
        if trait_task is not None:
            insert_at = tasks.index(trait_task) + 1
            tasks.insert(
                insert_at,
                ResearchTask(
                    id=_next_task_id(tasks, "T_info"),
                    title="本地数据库查询：候选基因详细信息",
                    question="Query detailed information and functional annotations for candidate genes from upstream results.",
                    purpose="Use candidate gene IDs from the trait query to retrieve local gene information.",
                    dependencies=[trait_task.id],
                    tools=["query_gene_info"],
                ),
            )


def _message_needs_trait2gene(text: str) -> bool:
    markers = (
        "which genes",
        "candidate genes",
        "trait-associated genes",
        "related genes",
        "哪些基因",
        "相关基因",
        "候选基因",
        "耐盐",
        "耐冷",
        "抗旱",
        "产量",
    )
    return any(marker in text for marker in markers)


def _message_needs_gene_info(text: str) -> bool:
    markers = (
        "function",
        "functional",
        "annotation",
        "details",
        "什么功能",
        "功能",
        "详细信息",
        "说明",
    )
    return any(marker in text for marker in markers)


def _next_task_id(tasks: list[ResearchTask], prefix: str) -> str:
    existing = {task.id for task in tasks}
    if prefix not in existing:
        return prefix
    index = 2
    while f"{prefix}_{index}" in existing:
        index += 1
    return f"{prefix}_{index}"


def _append_dependency(task: ResearchTask, dependency: str) -> None:
    if dependency and dependency != task.id and dependency not in task.dependencies:
        task.dependencies.append(dependency)


def _infer_task_tools(task_text: str, catalog: list[dict[str, Any]]) -> list[str]:
    if _is_synthesis_task(task_text):
        return []
    by_name = {str(item.get("name") or ""): item for item in catalog}
    if _looks_like_search_task(task_text):
        return [
            name
            for name in ("Search Query Rewriter", "Tavily Search", "Quark Search")
            if name in by_name
        ]
    matching_skills = [
        str(item.get("name") or "")
        for item in catalog
        if item.get("type") == "skill"
        and _tool_matches_task(task_text, item)
        and _tool_allowed_for_task(str(item.get("name") or ""), task_text)
    ]
    return _dedupe_tool_names(matching_skills)[:2]


def _tool_allowed_for_task(name: str, task_text: str) -> bool:
    if name in SEARCH_TOOL_NAMES:
        return True
    rules = {
        "blast_query": ("blast", "sequence", "fasta", "alignment", "序列", "比对"),
        "primer_query": ("primer", "pcr", "qpcr", "引物"),
        "differential_protein_analysis": ("protein", "proteomics", "差异蛋白", "蛋白组", "蛋白质组"),
        "differential_transcriptomics_analysis": ("transcript", "transcriptomics", "rna-seq", "差异表达", "转录组"),
        "gene_mutant_query": ("mutant", "mutation", "t-dna", "ems", "stock", "突变", "突变体"),
        "trait2gene_query": ("trait", "phenotype", "tolerance", "resistance", "yield", "相关基因", "候选基因", "哪些基因", "耐盐", "耐冷", "抗旱", "产量", "性状"),
        "rice_trait_records_query": ("trait", "phenotype", "rice trait", "水稻性状", "性状记录"),
        "query_gene_info": ("info", "detail", "details", "annotation", "function", "basic", "gene information", "详细", "信息", "注释", "功能", "候选基因"),
        "gene_phenotype_prediction": ("phenotype prediction", "predict", "可能关联", "表型预测", "预测"),
        "query_gene_function_research_path": ("research path", "function path", "研究路径", "功能研究路径"),
    }
    markers = rules.get(name)
    if markers is None:
        return True
    return any(marker in task_text for marker in markers)


def _tool_matches_task(task_text: str, tool: dict[str, Any]) -> bool:
    haystack = " ".join(
        str(tool.get(key) or "").lower()
        for key in ("name", "description", "trigger")
    )
    tokens = [token for token in re.split(r"[^a-z0-9_\u4e00-\u9fff]+", task_text) if len(token) >= 3]
    return any(token in haystack for token in tokens)


def _looks_like_search_task(task_text: str) -> bool:
    markers = (
        "search",
        "literature",
        "public",
        "latest",
        "web",
        "论文",
        "文献",
        "公共",
        "最新",
        "搜索",
        "检索",
    )
    return any(marker in task_text for marker in markers)


def _is_synthesis_task(task_text: str) -> bool:
    markers = (
        "synthesis",
        "synthesize",
        "integrate",
        "integration",
        "cross-check",
        "cross validate",
        "summary",
        "conclusion",
        "整合",
        "综合",
        "交叉验证",
        "汇总",
        "总结",
        "结论",
    )
    return any(marker in task_text for marker in markers)


def _dedupe_tool_names(names: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for name in names:
        if not name or name in seen:
            continue
        seen.add(name)
        result.append(name)
    return result


def _task_uses_search(task: ResearchTask) -> bool:
    return any(tool in SEARCH_TOOL_NAMES for tool in task.tools)


def _task_uses_skill(task: ResearchTask) -> bool:
    return any(tool and tool not in SEARCH_TOOL_NAMES for tool in task.tools)


def _research_tools(providers: list[str]) -> list[str]:
    tools = ["Search Query Rewriter"]
    provider_names = [str(provider).strip().lower() for provider in providers if str(provider).strip()]
    if not provider_names:
        provider_names = ["tavily", "quark"]
    labels = {
        "tavily": "Tavily Search",
        "quark": "Quark Search",
        "aliyun": "Quark Search",
        "opensearch": "Quark Search",
    }
    for provider in provider_names:
        label = labels.get(provider, provider)
        if label not in tools:
            tools.append(label)
    return tools
