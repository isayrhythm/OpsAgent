from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any


RetryPredicate = Callable[[BaseException], bool]
ResultRetryPredicate = Callable[[Any], bool]


@dataclass(frozen=True)
class ToolRetryPolicy:
    # 默认保守：工具不主动重试。需要重试的工具自己提高 max_attempts，
    # 并用 retry_if_exception / retry_exceptions 缩小可重试错误范围。
    max_attempts: int = 1
    initial_delay_seconds: float = 0.2
    backoff_multiplier: float = 2.0
    # retry_exceptions 是第一层类型筛选；retry_if_exception 可以继续根据
    # HTTP status code 这类细节做最终判断。
    retry_exceptions: tuple[type[BaseException], ...] = (Exception,)
    # fatal_exceptions 永远不重试，即使命中了 retry_exceptions。
    # 适合配置错误、权限错误、安全拦截这类重试也没意义的失败。
    fatal_exceptions: tuple[type[BaseException], ...] = ()
    retry_if_exception: RetryPredicate | None = None
    # 有些工具不会抛异常，而是在结构化结果里返回失败，例如 status=failed。
    # 这个钩子允许调用方基于结果内容决定是否重试。
    retry_if_result: ResultRetryPredicate | None = None
    # 老调用链依赖原始异常类型时设为 False；需要统一 ToolRunnerError
    # 和 attempts 元信息时保留 True。
    wrap_exceptions: bool = True


@dataclass(frozen=True)
class ToolAttempt:
    attempt: int
    status: str
    error: str = ""
    retry: bool = False


class ToolRunnerError(RuntimeError):
    def __init__(self, tool_name: str, attempts: list[ToolAttempt], cause: BaseException) -> None:
        super().__init__(f"{tool_name} failed after {len(attempts)} attempt(s): {cause}")
        self.tool_name = tool_name
        self.attempts = attempts
        self.__cause__ = cause


DEFAULT_TOOL_RETRY_POLICY = ToolRetryPolicy(max_attempts=1)
TRANSIENT_TOOL_RETRY_POLICY = ToolRetryPolicy(max_attempts=3)


async def run_tool(
    tool_name: str,
    call: Callable[[], Awaitable[Any]],
    *,
    policy: ToolRetryPolicy | None = None,
    on_attempt: Callable[[ToolAttempt], Awaitable[None]] | None = None,
) -> Any:
    """按策略执行一次工具调用，并在需要时重试。

    runner 不关心具体是搜索、skill 还是命令。调用方只传入一个无参数
    async 函数和 retry policy；这里统一处理 attempts、退避等待、
    结果型重试，以及最终异常形状。
    """
    retry_policy = policy or DEFAULT_TOOL_RETRY_POLICY
    max_attempts = max(1, int(retry_policy.max_attempts))
    attempts: list[ToolAttempt] = []
    last_result: Any = None

    for attempt_number in range(1, max_attempts + 1):
        try:
            result = await call()
        except BaseException as exc:
            # 异常型重试主要处理网络抖动、timeout、临时服务错误。
            # 永久失败要通过 fatal_exceptions 或 retry_if_exception 排除。
            should_retry = attempt_number < max_attempts and _should_retry_exception(exc, retry_policy)
            attempt = ToolAttempt(
                attempt=attempt_number,
                status="error",
                error=str(exc),
                retry=should_retry,
            )
            attempts.append(attempt)
            if on_attempt is not None:
                await on_attempt(attempt)
            if not should_retry:
                if not retry_policy.wrap_exceptions:
                    raise
                raise ToolRunnerError(tool_name, attempts, exc) from exc
            await _sleep_before_retry(retry_policy, attempt_number)
            continue

        should_retry_result = (
            attempt_number < max_attempts
            and retry_policy.retry_if_result is not None
            and retry_policy.retry_if_result(result)
        )
        # 结果型重试用于那些不抛异常、而是在 payload 里表达失败的工具，
        # 例如 {"status": "failed"}。
        attempt = ToolAttempt(
            attempt=attempt_number,
            status="result",
            retry=should_retry_result,
        )
        attempts.append(attempt)
        if on_attempt is not None:
            await on_attempt(attempt)
        last_result = result
        if not should_retry_result:
            return result
        await _sleep_before_retry(retry_policy, attempt_number)

    return last_result


def _should_retry_exception(exc: BaseException, policy: ToolRetryPolicy) -> bool:
    if isinstance(exc, policy.fatal_exceptions):
        return False
    if policy.retry_if_exception is not None:
        return bool(policy.retry_if_exception(exc))
    return isinstance(exc, policy.retry_exceptions)


async def _sleep_before_retry(policy: ToolRetryPolicy, attempt_number: int) -> None:
    delay = max(0.0, policy.initial_delay_seconds) * (max(1.0, policy.backoff_multiplier) ** (attempt_number - 1))
    if delay:
        await asyncio.sleep(delay)
