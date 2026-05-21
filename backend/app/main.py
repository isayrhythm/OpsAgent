from __future__ import annotations

import asyncio

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse

from backend.app.memory.store import MemoryStore
from backend.app.schemas import ChatRequest, ChatResponse, SkillSummary, UploadResponse
from backend.app.services.differential_protein import DifferentialProteinError, artifact_path
from backend.app.services.skill_loader import load_skills
from backend.app.services.task_manager import TaskManager
from backend.app.services.upload_intake_manager import UploadIntakeManager


app = FastAPI(title="OpsAgent API", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

tasks = TaskManager()
upload_intakes = UploadIntakeManager()
memory = MemoryStore()


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
    task_id = tasks.create_task(
        request.message,
        request.user_id,
        request.session_id,
        request.history,
        request.attachments,
        request.detached_files,
        request.web_search,
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
async def task_events(task_id: str) -> StreamingResponse:
    state = tasks.get(task_id)
    if state is None:
        raise HTTPException(status_code=404, detail="Task not found")

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
