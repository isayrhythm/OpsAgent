import asyncio

import pytest

from backend.app.tools.tool_runner import ToolRetryPolicy, ToolRunnerError, run_tool


def test_tool_runner_retries_transient_exception() -> None:
    calls = {"count": 0}

    async def flaky():
        calls["count"] += 1
        if calls["count"] < 3:
            raise TimeoutError("temporary")
        return {"ok": True}

    result = asyncio.run(
        run_tool(
            "demo",
            flaky,
            policy=ToolRetryPolicy(max_attempts=3, initial_delay_seconds=0),
        )
    )

    assert result == {"ok": True}
    assert calls["count"] == 3


def test_tool_runner_does_not_retry_fatal_exception() -> None:
    calls = {"count": 0}

    async def failing():
        calls["count"] += 1
        raise PermissionError("no")

    with pytest.raises(ToolRunnerError):
        asyncio.run(
            run_tool(
                "demo",
                failing,
                policy=ToolRetryPolicy(
                    max_attempts=3,
                    initial_delay_seconds=0,
                    fatal_exceptions=(PermissionError,),
                ),
            )
        )

    assert calls["count"] == 1


def test_tool_runner_can_retry_on_result() -> None:
    calls = {"count": 0}

    async def command_like():
        calls["count"] += 1
        return {"status": "failed" if calls["count"] == 1 else "completed"}

    result = asyncio.run(
        run_tool(
            "command",
            command_like,
            policy=ToolRetryPolicy(
                max_attempts=2,
                initial_delay_seconds=0,
                retry_if_result=lambda value: value.get("status") == "failed",
            ),
        )
    )

    assert result["status"] == "completed"
    assert calls["count"] == 2


def test_tool_runner_can_preserve_original_exception() -> None:
    async def failing():
        raise ValueError("raw")

    with pytest.raises(ValueError, match="raw"):
        asyncio.run(
            run_tool(
                "demo",
                failing,
                policy=ToolRetryPolicy(max_attempts=1, wrap_exceptions=False),
            )
        )
