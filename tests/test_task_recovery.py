import asyncio
from dataclasses import replace
from unittest.mock import AsyncMock

import pytest

from caiocore.task_recovery import (
    AttemptOutput, EventKind as E, Limits, Mode, Plan, RecoveryRun, Status,
    Step, UnsupportedMode,
)


def run(coro):
    return asyncio.run(coro)


def plan(*steps, total=1, attempt=0.2, maximum=10):
    return Plan(tuple(steps), Limits(total, attempt, maximum), Mode.SIMULATION)


def kinds(r):
    return [e.kind for e in r.events]


def test_real_mode_denied_and_inputs_fail_closed():
    async def op():
        return AttemptOutput(True, 'actual')
    async def verify():
        return True
    step = Step(op, verify)
    with pytest.raises(UnsupportedMode):
        Plan((step,), Limits(1, 1, 1))
    with pytest.raises(UnsupportedMode):
        Plan((step,), Limits(1, 1, 1), Mode.REAL)
    for args in [(0, 1, 1), (float('nan'), 1, 1), (1, float('inf'), 1),
                 (86401, 1, 1), (1, 3601, 1), (1, 1, 0), (1, 1, True), (1, 1, 11)]:
        with pytest.raises(ValueError):
            Limits(*args)
    with pytest.raises(ValueError):
        Step(lambda: AttemptOutput(True), verify)
    with pytest.raises(ValueError):
        Step(op, op)
    with pytest.raises(ValueError):
        Step(op, verify, idempotent=1)
    with pytest.raises(ValueError):
        Plan((step, step), Limits(1, 1, 1), Mode.SIMULATION)


def test_actual_output_independent_verifier_and_events():
    state = []
    async def op():
        state.append('effect')
        return AttemptOutput(True, {'real': 42})
    async def verify():
        return state == ['effect']
    r = RecoveryRun(plan(Step(op, verify)))
    result = run(r.execute())
    assert (result.status, result.attempts, result.value) == (Status.SUCCEEDED, 1, {'real': 42})
    assert kinds(r) == [E.ATTEMPT_STARTED, E.OPERATION_RETURNED, E.VERIFICATION_STARTED, E.VERIFIED, E.SUCCEEDED]
    with pytest.raises(RuntimeError):
        run(r.execute())


def test_injected_fallback_uses_only_explicit_plan():
    first = AsyncMock(side_effect=RuntimeError('private exception'))
    second = AsyncMock(return_value=AttemptOutput(True, 'from second'))
    verify = AsyncMock(return_value=True)
    r = RecoveryRun(plan(Step(first, verify, True), Step(second, verify)))
    result = run(r.execute())
    assert result.status is Status.SUCCEEDED and result.value == 'from second' and result.attempts == 2
    first.assert_awaited_once()
    second.assert_awaited_once()
    verify.assert_awaited_once()
    assert kinds(r) == [E.ATTEMPT_STARTED, E.OPERATION_FAILED, E.ATTEMPT_STARTED,
                        E.OPERATION_RETURNED, E.VERIFICATION_STARTED, E.VERIFIED, E.SUCCEEDED]
    assert 'private exception' not in repr(r.events) + repr(result)


@pytest.mark.parametrize('bad', [AttemptOutput(False, 'fake success'), AttemptOutput(1, 'fake'), None, 'success', True])
def test_bad_operation_output_never_verifies_or_succeeds(bad):
    operation = AsyncMock(return_value=bad)
    verify = AsyncMock(return_value=True)
    r = RecoveryRun(plan(Step(operation, verify)))
    result = run(r.execute())
    assert result.status is Status.FAILED and result.value is None
    verify.assert_not_awaited()
    assert kinds(r) == [E.ATTEMPT_STARTED, E.OPERATION_FAILED, E.FAILED]


@pytest.mark.parametrize('reply', [False, None, 1, 'true'])
def test_verification_requires_literal_true(reply):
    op = AsyncMock(return_value=AttemptOutput(True, 'secret result'))
    verify = AsyncMock(return_value=reply)
    r = RecoveryRun(plan(Step(op, verify)))
    result = run(r.execute())
    assert result.status is Status.FAILED and result.value is None
    assert E.VERIFICATION_REJECTED in kinds(r) and E.SUCCEEDED not in kinds(r)
    assert 'secret result' not in repr(r.events) + repr(result)


def test_verifier_exception_never_success():
    op = AsyncMock(return_value=AttemptOutput(True, 'not verified'))
    verify = AsyncMock(side_effect=RuntimeError('credential'))
    r = RecoveryRun(plan(Step(op, verify)))
    assert run(r.execute()).status is Status.FAILED
    assert kinds(r)[-2:] == [E.VERIFICATION_FAILED, E.FAILED]
    assert 'credential' not in repr(r.events)


@pytest.mark.parametrize('failure', ['exception', 'returned_false', 'verify_false', 'verify_exception', 'timeout', 'verify_timeout'])
def test_never_retry_or_fallback_after_possible_non_idempotent_mutation(failure):
    async def scenario():
        effects = []
        async def mutate():
            effects.append(1)
            if failure == 'exception':
                raise RuntimeError('failed after mutation')
            if failure == 'timeout':
                await asyncio.sleep(10)
            return AttemptOutput(failure != 'returned_false', 'value')
        async def verify():
            if failure == 'verify_exception':
                raise RuntimeError('unknown state')
            if failure == 'verify_timeout':
                await asyncio.sleep(10)
            return False
        fallback = AsyncMock(return_value=AttemptOutput(True))
        fallback_verify = AsyncMock(return_value=True)
        r = RecoveryRun(plan(Step(mutate, verify), Step(fallback, fallback_verify), attempt=0.01))
        result = await r.execute()
        assert result.status is Status.FAILED
        assert effects == [1] and result.attempts == 1
        fallback.assert_not_awaited()
        fallback_verify.assert_not_awaited()
        assert E.SUCCEEDED not in kinds(r)
    run(scenario())


def test_timeout_cancels_inline_no_orphan_and_allows_preplanned_idempotent_fallback():
    async def scenario():
        cancelled = []
        async def slow():
            try:
                await asyncio.sleep(10)
            finally:
                cancelled.append(True)
        async def fallback():
            return AttemptOutput(True, 'worked')
        async def verify():
            return True
        before = set(asyncio.all_tasks())
        r = RecoveryRun(plan(Step(slow, verify, True), Step(fallback, verify), attempt=0.01))
        result = await r.execute()
        assert result.status is Status.SUCCEEDED and result.value == 'worked'
        assert cancelled == [True] and set(asyncio.all_tasks()) == before
        assert E.OPERATION_TIMEOUT in kinds(r)
    run(scenario())


def test_late_result_after_suppressed_timeout_is_never_success():
    async def slow():
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            return AttemptOutput(True, 'late')
    verify = AsyncMock(return_value=True)
    r = RecoveryRun(plan(Step(slow, verify), attempt=0.01))
    assert run(r.execute()).status is Status.FAILED
    verify.assert_not_awaited()
    assert E.OPERATION_TIMEOUT in kinds(r) and E.SUCCEEDED not in kinds(r)


def test_total_deadline_prevents_fallback():
    async def slow():
        await asyncio.sleep(10)
    verify = AsyncMock(return_value=True)
    fallback = AsyncMock(return_value=AttemptOutput(True))
    r = RecoveryRun(plan(Step(slow, verify, True), Step(fallback, verify), total=0.01, attempt=0.5))
    result = run(r.execute())
    assert result.status is Status.DEADLINE and result.attempts == 1
    fallback.assert_not_awaited()
    assert kinds(r)[-1] == E.DEADLINE


@pytest.mark.parametrize('stage', ['operation', 'verification', 'suppress_cancel'])
def test_external_cancellation_propagates_and_never_retries(stage):
    async def scenario():
        entered = asyncio.Event()
        cleaned = []
        async def wait():
            entered.set()
            try:
                await asyncio.sleep(10)
            except asyncio.CancelledError:
                if stage == 'suppress_cancel':
                    return AttemptOutput(True, 'late')
                raise
            finally:
                cleaned.append(True)
        async def op():
            if stage in ('operation', 'suppress_cancel'):
                return await wait()
            return AttemptOutput(True)
        async def verify():
            if stage == 'verification':
                await wait()
            return True
        fallback = AsyncMock(return_value=AttemptOutput(True))
        r = RecoveryRun(plan(Step(op, verify, True), Step(fallback, verify)))
        task = asyncio.create_task(r.execute())
        await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert cleaned == [True]
        assert kinds(r)[-1] is E.CANCELLED
        assert E.SUCCEEDED not in kinds(r)
        fallback.assert_not_awaited()
    run(scenario())


def test_retry_only_predefined_attempt_count():
    op = AsyncMock(return_value=AttemptOutput(False))
    verify = AsyncMock(return_value=True)
    r = RecoveryRun(plan(Step(op, verify, True), Step(op, verify, True), maximum=2))
    result = run(r.execute())
    assert result.status is Status.FAILED and result.attempts == 2
    assert op.await_count == 2 and kinds(r).count(E.ATTEMPT_STARTED) == 2
    verify.assert_not_awaited()
