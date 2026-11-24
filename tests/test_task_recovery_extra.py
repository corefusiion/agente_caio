import asyncio
from unittest.mock import AsyncMock

import pytest

from caiocore.task_recovery import AttemptOutput, EventKind, Limits, Mode, Plan, RecoveryRun, Status, Step


def test_injected_timeout_exception_is_failure_not_elapsed_timeout():
    async def op():
        raise TimeoutError('adapter timeout')
    async def verify():
        return True
    r = RecoveryRun(Plan((Step(op, verify),), Limits(1, 1, 1), Mode.SIMULATION))
    assert asyncio.run(r.execute()).status is Status.FAILED
    assert [e.kind for e in r.events] == [EventKind.ATTEMPT_STARTED, EventKind.OPERATION_FAILED, EventKind.FAILED]


def test_verifier_timeout_waits_for_cleanup_without_orphan():
    async def scenario():
        cleaned = []
        async def op():
            return AttemptOutput(True, 'should not escape')
        async def verify():
            try:
                await asyncio.sleep(20)
            finally:
                cleaned.append(True)
        before = set(asyncio.all_tasks())
        r = RecoveryRun(Plan((Step(op, verify),), Limits(1, .01, 1), Mode.SIMULATION))
        result = await r.execute()
        assert result.status is Status.FAILED and result.value is None
        assert cleaned == [True] and set(asyncio.all_tasks()) == before
        assert EventKind.VERIFICATION_TIMEOUT in [e.kind for e in r.events]
    asyncio.run(scenario())


def test_cancellation_raised_by_adapter_is_not_a_retry():
    async def op():
        raise asyncio.CancelledError
    async def verify():
        return True
    backup = AsyncMock(return_value=AttemptOutput(True))
    r = RecoveryRun(Plan((Step(op, verify, True), Step(backup, verify)), Limits(1, 1, 2), Mode.SIMULATION))
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(r.execute())
    backup.assert_not_awaited()
    assert [e.kind for e in r.events] == [EventKind.ATTEMPT_STARTED, EventKind.CANCELLED]


def test_fallback_after_idempotent_rejection_uses_independent_verification():
    first = AsyncMock(return_value=AttemptOutput(True, 'unverified'))
    first_verify = AsyncMock(return_value=False)
    second = AsyncMock(return_value=AttemptOutput(True, 'verified'))
    second_verify = AsyncMock(return_value=True)
    r = RecoveryRun(Plan((Step(first, first_verify, True), Step(second, second_verify)), Limits(1, 1, 2), Mode.SIMULATION))
    result = asyncio.run(r.execute())
    assert (result.status, result.value, result.attempts) == (Status.SUCCEEDED, 'verified', 2)
    assert EventKind.VERIFICATION_REJECTED in [e.kind for e in r.events]
    first_verify.assert_awaited_once()
    second_verify.assert_awaited_once()
