# Roadmap

## Current MVP Status

The first-stage OpsAgent MVP is functionally complete:

- General chat defaults to normal LLM conversation and does not execute code.
- Skill routing is automatic and only triggers when the user request matches a skill.
- Skills are dynamically loaded from `skill/*.md`.
- The gene expression skill can generate and execute Python code against the sample CSV.
- Skill outputs are JSON-serializable and passed back to the final answer model.
- LangGraph coordinates request understanding, routing, skill execution, and final response.
- Task execution is asynchronous through an in-memory task manager and SSE queue.
- SSE supports `progress`, `answer_delta`, `result`, and `end` events.
- DeepSeek routing uses the flash model, while normal chat and final answers use the pro model.
- DeepSeek thinking mode is explicitly disabled.
- The frontend supports chat history, markdown rendering, streaming output, and local session persistence.
- Short-term backend memory is stored under `memory/short_term/{user_id}/conversations/{session_id}.json`.
- PDM is the project runner and dependency manager.

## Partially Complete

- Skill schema is documented through markdown frontmatter and contracts, but strict JSON Schema validation is not implemented yet.
- Worker execution is lightweight: `asyncio.create_task`, in-memory queues, and LangGraph. It is not a separate process or durable queue.
- Code execution has basic forbidden import/function checks, but it is not a full sandbox.
- Short-term memory exists; long-term memory is only a directory placeholder.
- Upload directories are reserved, but upload APIs and frontend controls are not implemented yet.

## Not Implemented Yet

- Task cancellation API.
- Retry policy for failed tasks.
- Backend session list/history APIs for the frontend.
- File upload API and upload-aware skills.
- Strict input/output JSON Schema validation for skills.
- Per-skill execution isolation or a real sandbox.
- Durable task queue or separate worker process.

## Recommended Next Steps

1. Add task cancellation and task status APIs.
2. Add backend conversation list/history APIs so the frontend does not rely only on localStorage.
3. Add file upload API and connect uploaded files to short-term memory.
4. Add strict Skill JSON Schema validation.
5. Improve code execution isolation for generated Python.
