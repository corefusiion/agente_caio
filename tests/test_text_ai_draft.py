import asyncio
from dataclasses import replace

import pytest

from caiocore.providers.base import LLMResponse, ToolCallRequest
from caiocore.task_queue.text_ai_draft import (
    Disabled, FakeProvider, InvalidResult, approve_for_test, execute_real,
    exercise_offline, prepare,
)


def make():
    return prepare('task-1', 'Example', 'https://example.invalid/v1', 'model-1', 'Um texto pessoal')


def test_offline_request_only_user_text_and_no_tools():
    d = make()
    p = FakeProvider(LLMResponse(content='Resumo pronto'))
    assert asyncio.run(exercise_offline(d, approve_for_test(d), p)) == 'Resumo pronto'
    assert p.calls == [([{'role': 'user', 'content': d.text}], None, d.model, 512, 0.2)]


@pytest.mark.parametrize('field,value', [
    ('task_id', 'task-2'), ('provider', 'Other'), ('endpoint', 'https://other.invalid/v1'),
    ('model', 'model-2'), ('text', 'outro conteúdo'),
])
def test_changed_disclosure_invalidates_test_approval(field, value):
    d = make()
    p = FakeProvider(LLMResponse(content='ok'))
    with pytest.raises(ValueError):
        asyncio.run(exercise_offline(replace(d, **{field: value}), approve_for_test(d), p))
    assert p.calls == []


@pytest.mark.parametrize('url', ['http://example.com', 'https://user:pass@example.com/',
                                 'https://example.com/?key=secret', 'https://example.com/#x'])
def test_disclosure_rejects_unsafe_endpoint(url):
    with pytest.raises(ValueError):
        prepare('1', 'X', url, 'm', 'text')


@pytest.mark.parametrize('response', [
    LLMResponse(content='secret exception', finish_reason='error'),
    LLMResponse(content='partial', finish_reason='length'),
    LLMResponse(content='hello', tool_calls=[ToolCallRequest('1', 'x', {})]),
    LLMResponse(content=None), LLMResponse(content='x' * 2001),
])
def test_unusable_result_is_generic(response):
    d = make()
    with pytest.raises(InvalidResult, match='invalid provider response'):
        asyncio.run(exercise_offline(d, approve_for_test(d), FakeProvider(response)))


def test_timeout_no_retry():
    d = make()
    p = FakeProvider(LLMResponse(content='late'), delay=1)
    with pytest.raises(TimeoutError):
        asyncio.run(exercise_offline(d, approve_for_test(d), p, timeout=0.001))
    assert len(p.calls) == 1


def test_cancel_propagates_and_no_retry():
    async def run():
        d = make()
        p = FakeProvider(LLMResponse(content='late'), delay=100)
        task = asyncio.create_task(exercise_offline(d, approve_for_test(d), p))
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert len(p.calls) == 1
    asyncio.run(run())


def test_real_disabled_and_fake_only():
    with pytest.raises(Disabled):
        asyncio.run(execute_real())
    d = make()
    class Untrusted:
        async def chat(self, **kwargs):
            raise AssertionError('must not run')
    with pytest.raises(ValueError):
        asyncio.run(exercise_offline(d, approve_for_test(d), Untrusted()))
