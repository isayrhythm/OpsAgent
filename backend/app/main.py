from __future__ import annotations

import asyncio

from fastapi import FastAPI, File, Form, Header, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse

from backend.app.config import CORS_ORIGINS
from backend.app.memory.store import MemoryStore
from backend.app.runs.manager import RunManager
from backend.app.schemas import ChatRequest, ChatResponse, SkillSummary, UploadResponse
from backend.app.skill_tools.differential_protein import DifferentialProteinError, artifact_path
from backend.app.services.skill_loader import load_skills
from backend.app.agents.task_manager import TaskManager
from backend.app.services.upload_intake_manager import UploadIntakeManager


app = FastAPI(title="OpsAgent API", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

memory = MemoryStore()
runs = RunManager(memory=memory)
tasks = TaskManager(memory=memory, run_manager=runs)
upload_intakes = UploadIntakeManager()


@app.get("/api/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/skills", response_model=list[SkillSummary])
async def skills() -> list[SkillSummary]:
    return [
        SkillSummary(
            name=skill.name,
            description=skill.description,
            version=skill.version,
            trigger=skill.trigger,
            execution_mode=skill.execution_mode,
            data_paths=skill.data_paths,
            path=str(skill.path),
        )
        for skill in load_skills()
    ]


@app.post("/api/chat", response_model=ChatResponse)
async def chat(request: ChatRequest) -> ChatResponse:
    try:
        attachments = memory.resolve_chat_attachments(request.user_id, request.session_id, request.attachments)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    task_id = tasks.create_task(
        request.message,
        request.user_id,
        request.session_id,
        request.history,
        attachments,
        request.detached_files,
        request.web_search,
        request.web_search_mode,
        request.web_search_providers,
    )
    return ChatResponse(task_id=task_id, events_url=f"/api/tasks/{task_id}/events")


@app.post("/api/uploads", response_model=UploadResponse)
async def upload_files(
    user_id: str = Form(default="default"),
    session_id: str = Form(...),
    files: list[UploadFile] = File(...),
) -> UploadResponse:
    saved = []
    for file in files:
        summary = memory.save_upload(user_id, session_id, file.filename or "upload", file.content_type, file.file)
        saved.append(summary)
    task_id = upload_intakes.create_task(saved, memory.update_upload_metadata)
    return UploadResponse(task_id=task_id, events_url=f"/api/uploads/{task_id}/events", files=saved)


@app.get("/api/tasks/{task_id}/events")
async def task_events(task_id: str, last_event_id: str | None = Header(default=None)) -> StreamingResponse:
    state = tasks.get(task_id)
    if state is None:
        raise HTTPException(status_code=404, detail="Task not found")

    async def stream():
        cursor = _event_cursor(last_event_id)
        while True:
            while cursor < len(state.events):
                event = state.events[cursor]
                cursor += 1
                yield f"id: {cursor}\nevent: {event.type}\ndata: {event.model_dump_json()}\n\n"
            if state.done:
                yield "event: end\ndata: {}\n\n"
                break
            async with state.condition:
                if cursor < len(state.events) or state.done:
                    continue
                try:
                    await asyncio.wait_for(state.condition.wait(), timeout=15)
                except asyncio.TimeoutError:
                    pass
            if cursor >= len(state.events) and not state.done:
                yield "event: ping\ndata: {}\n\n"

    return StreamingResponse(stream(), media_type="text/event-stream")


@app.post("/api/tasks/{task_id}/cancel")
async def cancel_task(task_id: str) -> dict[str, str | bool]:
    state = tasks.get(task_id)
    if state is None:
        raise HTTPException(status_code=404, detail="Task not found")
    return {"task_id": task_id, "cancelled": tasks.cancel(task_id)}


@app.get("/api/runs")
async def background_runs(user_id: str = "default", session_id: str | None = None) -> list[dict[str, object]]:
    return [state.summary() for state in runs.list_for_session(user_id, session_id)]


@app.get("/api/runs/{run_id}")
async def background_run(run_id: str) -> dict[str, object]:
    state = runs.get(run_id)
    if state is None:
        raise HTTPException(status_code=404, detail="Background run not found")
    return state.summary(include_result=True)


@app.get("/api/runs/{run_id}/events")
async def background_run_events(run_id: str, last_event_id: str | None = Header(default=None)) -> StreamingResponse:
    state = runs.get(run_id)
    if state is None:
        raise HTTPException(status_code=404, detail="Background run not found")

    async def stream():
        cursor = _event_cursor(last_event_id)
        while True:
            while cursor < len(state.events):
                event = state.events[cursor]
                cursor += 1
                yield f"id: {cursor}\nevent: {event.type}\ndata: {event.model_dump_json()}\n\n"
            if state.done:
                yield "event: end\ndata: {}\n\n"
                break
            async with state.condition:
                if cursor < len(state.events) or state.done:
                    continue
                try:
                    await asyncio.wait_for(state.condition.wait(), timeout=15)
                except asyncio.TimeoutError:
                    pass
            if cursor >= len(state.events) and not state.done:
                yield "event: ping\ndata: {}\n\n"

    return StreamingResponse(stream(), media_type="text/event-stream")


@app.post("/api/runs/{run_id}/cancel")
async def cancel_background_run(run_id: str) -> dict[str, str | bool]:
    state = runs.get(run_id)
    if state is None:
        raise HTTPException(status_code=404, detail="Background run not found")
    return {"run_id": run_id, "cancelled": runs.cancel(run_id)}


@app.get("/api/uploads/{task_id}/events")
async def upload_intake_events(task_id: str) -> StreamingResponse:
    state = upload_intakes.get(task_id)
    if state is None:
        raise HTTPException(status_code=404, detail="Upload intake task not found")

    async def stream():
        while True:
            if state.done and state.queue.empty():
                yield "event: end\ndata: {}\n\n"
                break
            try:
                event = await asyncio.wait_for(state.queue.get(), timeout=15)
            except asyncio.TimeoutError:
                yield "event: ping\ndata: {}\n\n"
                continue
            yield f"event: {event.type}\ndata: {event.model_dump_json()}\n\n"

    return StreamingResponse(stream(), media_type="text/event-stream")


@app.get("/api/artifacts/{run_id}/{filename:path}")
async def artifact(run_id: str, filename: str) -> FileResponse:
    try:
        path = artifact_path(run_id, filename)
    except DifferentialProteinError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not path.exists() or not path.is_file():
        raise HTTPException(status_code=404, detail="Artifact not found")
    return FileResponse(path)


def _event_cursor(last_event_id: str | None) -> int:
    try:
        return max(0, int(last_event_id or "0"))
    except ValueError:
        return 0
