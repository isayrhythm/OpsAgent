from __future__ import annotations

import asyncio

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

from backend.app.schemas import ChatRequest, ChatResponse, SkillSummary
from backend.app.services.skill_loader import load_skills
from backend.app.services.task_manager import TaskManager


app = FastAPI(title="OpsAgent API", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

tasks = TaskManager()


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
    task_id = tasks.create_task(request.message, request.user_id, request.session_id, request.history)
    return ChatResponse(task_id=task_id, events_url=f"/api/tasks/{task_id}/events")


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
