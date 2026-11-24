"""In-memory, simulation-only recovery controller; no I/O or tool integration.

Injected callables must be trusted, cooperative asyncio coroutine functions.
This module is not a sandbox or an authorization mechanism.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from enum import Enum
import inspect
import math
from typing import Any, Awaitable, Callable


class UnsupportedMode(ValueError):
    """Real actions are intentionally not supported."""


class Mode(Enum):
    REAL = "real"
    SIMULATION = "simulation"


class Status(Enum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    DEADLINE = "deadline"


class EventKind(Enum):
    ATTEMPT_STARTED = "attempt_started"
    OPERATION_RETURNED = "operation_returned"
    OPERATION_FAILED = "operation_failed"
    OPERATION_TIMEOUT = "operation_timeout"
    VERIFICATION_STARTED = "verification_started"
    VERIFICATION_FAILED = "verification_failed"
    VERIFICATION_REJECTED = "verification_rejected"
    VERIFICATION_TIMEOUT = "verification_timeout"
    VERIFIED = "verified"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    DEADLINE = "deadline"
    CANCELLED = "cancelled"


@dataclass(frozen=True)
class Event:
    kind: EventKind
    attempt: int


@dataclass(frozen=True)
class AttemptOutput:
    ok: bool
    # Avoid accidentally disclosing a value through repr/logging.
    value: Any = field(default=None, repr=False)


@dataclass(frozen=True)
class Step:
    operation: Callable[[], Awaitable[AttemptOutput]] = field(repr=False)
    verify: Callable[[], Awaitable[bool]] = field(repr=False)
    # False is deliberately conservative. No further step follows a
    # non-idempotent attempt, even if it timed out or verification failed.
    idempotent: bool = False

    def __post_init__(self) -> None:
        if not inspect.iscoroutinefunction(self.operation) or not inspect.iscoroutinefunction(self.verify):
            raise ValueError("operation and verifier must be async functions")
        if self.operation == self.verify:
            raise ValueError("operation and verifier must be distinct functions")
        if type(self.idempotent) is not bool:
            raise ValueError("idempotent must be bool")


@dataclass(frozen=True)
class Limits:
    total_seconds: float
    attempt_seconds: float
    max_attempts: int

    def __post_init__(self) -> None:
        for value, maximum in ((self.total_seconds, 86400), (self.attempt_seconds, 3600)):
            if type(value) not in (int, float) or not 0 < value <= maximum or not math.isfinite(value):
                raise ValueError("invalid time limit")
        if type(self.max_attempts) is not int or not 1 <= self.max_attempts <= 10:
            raise ValueError("max_attempts must be an integer from 1 to 10")


@dataclass(frozen=True)
class Plan:
    steps: tuple[Step, ...]
    limits: Limits
    # An explicit simulation choice is mandatory; REAL is not available.
    mode: Mode = Mode.REAL

    def __post_init__(self) -> None:
        if self.mode is not Mode.SIMULATION:
            raise UnsupportedMode("only trusted offline simulations are supported")
        if type(self.limits) is not Limits:
            raise ValueError("invalid limits")
        if type(self.steps) is not tuple or not self.steps or len(self.steps) > self.limits.max_attempts:
            raise ValueError("plan must contain 1 to max_attempts steps")
        if any(type(step) is not Step for step in self.steps):
            raise ValueError("invalid step")


@dataclass(frozen=True)
class RunResult:
    status: Status
    attempts: int
    value: Any = field(default=None, repr=False)


class _StageTimeout(Exception):
    pass


class _StageFailure(Exception):
    pass


async def _invoke(fn: Callable[[], Awaitable[Any]], deadline: float) -> Any:
    """Run inline: no child task is created or abandoned on timeout.

    Cancellation is cooperative; a coroutine that blocks or suppresses cancellation
    can delay this function indefinitely. A late result is never accepted.
    """
    loop = asyncio.get_running_loop()
    task = asyncio.current_task()
    if task is not None and task.cancelling():
        raise asyncio.CancelledError
    remaining = deadline - loop.time()
    if remaining <= 0:
        raise _StageTimeout
    scope = asyncio.timeout(remaining)
    try:
        async with scope:
            value = await fn()
    except asyncio.CancelledError:
        raise
    except Exception:
        if task is not None and task.cancelling():
            raise asyncio.CancelledError from None
        if scope.expired() or loop.time() >= deadline:
            raise _StageTimeout from None
        raise _StageFailure from None
    if task is not None and task.cancelling():
        raise asyncio.CancelledError
    if scope.expired() or loop.time() >= deadline:
        raise _StageTimeout
    return value


class RecoveryRun:
    """One run; observable, sanitized events, and a result on completion.

    Cancellation records CANCELLED and is propagated; inspect events afterwards.
    Instances are single-use, including after cancellation.
    """

    def __init__(self, plan: Plan):
        if type(plan) is not Plan:
            raise ValueError("invalid plan")
        self._plan = plan
        self._events: list[Event] = []
        self._used = False

    @property
    def events(self) -> tuple[Event, ...]:
        return tuple(self._events)

    def _emit(self, kind: EventKind, attempt: int) -> None:
        self._events.append(Event(kind, attempt))

    async def execute(self) -> RunResult:
        if self._used:
            raise RuntimeError("run already used")
        self._used = True
        loop = asyncio.get_running_loop()
        total_deadline = loop.time() + self._plan.limits.total_seconds
        attempts = 0
        try:
            for step in self._plan.steps:
                if loop.time() >= total_deadline:
                    self._emit(EventKind.DEADLINE, attempts)
                    return RunResult(Status.DEADLINE, attempts)
                attempts += 1
                self._emit(EventKind.ATTEMPT_STARTED, attempts)
                attempt_deadline = min(total_deadline, loop.time() + self._plan.limits.attempt_seconds)
                try:
                    output = await _invoke(step.operation, attempt_deadline)
                except _StageTimeout:
                    self._emit(EventKind.OPERATION_TIMEOUT, attempts)
                except _StageFailure:
                    self._emit(EventKind.OPERATION_FAILED, attempts)
                else:
                    # Only a concrete, explicitly successful output qualifies.
                    if type(output) is not AttemptOutput or output.ok is not True:
                        self._emit(EventKind.OPERATION_FAILED, attempts)
                    else:
                        self._emit(EventKind.OPERATION_RETURNED, attempts)
                        self._emit(EventKind.VERIFICATION_STARTED, attempts)
                        try:
                            verified = await _invoke(step.verify, attempt_deadline)
                        except _StageTimeout:
                            self._emit(EventKind.VERIFICATION_TIMEOUT, attempts)
                        except _StageFailure:
                            self._emit(EventKind.VERIFICATION_FAILED, attempts)
                        else:
                            if verified is True:
                                self._emit(EventKind.VERIFIED, attempts)
                                self._emit(EventKind.SUCCEEDED, attempts)
                                return RunResult(Status.SUCCEEDED, attempts, output.value)
                            self._emit(EventKind.VERIFICATION_REJECTED, attempts)
                # A timeout, exception, or rejected verification does not prove
                # a mutation didn't happen. Never proceed to another action.
                if not step.idempotent:
                    break
            if loop.time() >= total_deadline:
                self._emit(EventKind.DEADLINE, attempts)
                return RunResult(Status.DEADLINE, attempts)
            self._emit(EventKind.FAILED, attempts)
            return RunResult(Status.FAILED, attempts)
        except asyncio.CancelledError:
            self._emit(EventKind.CANCELLED, attempts)
            raise
