"""SSE must never expose unscoped traces, even to an API-token holder.

All data and credentials below are synthetic. Exercise the actual ASGI route,
security middleware, broadcaster and tracer, with no external services.
"""
import asyncio

import httpx

from caiocore.agent.tracer import AgentTracer
from caiocore.agents.events import broadcaster
from caiocore.server.api import app


TOKEN = 'synthetic-api-token'
SECRETS = [
    'SESSION_A_PRIVATE_CONTENT', 'SESSION_B_PRIVATE_CONTENT',
    'SESSION_A_PRIVATE_ARGUMENTS', 'SESSION_B_PRIVATE_ARGUMENTS',
    'SESSION_A_PRIVATE_OUTPUT', 'SESSION_B_PRIVATE_OUTPUT',
    'SESSION_A_PRIVATE_PROMPT', 'SESSION_B_PRIVATE_PROMPT',
    'SESSION_A_PRIVATE_RESPONSE', 'SESSION_B_PRIVATE_RESPONSE',
]


def clear_security_env(monkeypatch):
    for key in ('CAIO_API_TOKEN', 'CAIO_ALLOWED_HOSTS', 'CAIO_ALLOWED_ORIGINS',
                'CAIO_EVOLUTION_WEBHOOK_TOKEN'):
        monkeypatch.delenv(key, raising=False)


def test_two_sessions_and_sensitive_trace_fields_never_reach_stream(monkeypatch, tmp_path):
    clear_security_env(monkeypatch)
    monkeypatch.setenv('CAIO_API_TOKEN', TOKEN)
    monkeypatch.setattr(broadcaster, 'subscribers', [])
    seen = []
    original_broadcast = broadcaster.broadcast

    async def capture(data):
        seen.append(data)
        await original_broadcast(data)

    async def forbidden_subscribe():
        raise AssertionError('disabled SSE must never subscribe to the global broadcaster')

    monkeypatch.setattr(broadcaster, 'broadcast', capture)
    monkeypatch.setattr(broadcaster, 'subscribe', forbidden_subscribe)

    async def run():
        tracer = AgentTracer(tmp_path)
        transport = httpx.ASGITransport(app=app, client=('127.0.0.1', 12345))
        async with httpx.AsyncClient(transport=transport, base_url='http://localhost',
                                     headers={'Authorization': f'Bearer {TOKEN}',
                                              'Accept': 'text/event-stream'}) as client:
            async def emit(session, content, args, output, prompt, response):
                await tracer.log_thought(session, 'synthetic-agent', 'tool_result', content,
                                         {'arguments': args, 'output': output})
                tracer.log_run(session, 'synthetic-agent', 'dashboard', prompt, response,
                               ['synthetic-tool'], 1.0)
                await asyncio.sleep(0)  # run_complete broadcast is scheduled as a task

            responses = await asyncio.gather(
                client.get('/api/tracing/stream', params={'session_id': 'session-A'}),
                emit('session-A', SECRETS[0], SECRETS[2], SECRETS[4], SECRETS[6], SECRETS[8]),
                client.get('/api/tracing/stream', params={'session_id': 'session-B'}),
                emit('session-B', SECRETS[1], SECRETS[3], SECRETS[5], SECRETS[7], SECRETS[9]),
                client.get('/api/tracing/stream'),
            )
            for result in responses:
                if result is None:
                    continue
                assert result.status_code == 503
                assert result.headers['cache-control'] == 'no-store'
                assert not result.headers['content-type'].startswith('text/event-stream')
                assert result.json()['detail'] == 'Tracing stream disabled: session isolation unavailable'
                assert all(secret not in result.text for secret in SECRETS)
                assert 'session-A' not in result.text and 'session-B' not in result.text
            assert broadcaster.subscribers == []
        assert len(seen) == 4
        assert {event['session_id'] for event in seen} == {'session-A', 'session-B'}
        assert all(secret in repr(seen) for secret in SECRETS)

    asyncio.run(run())


def test_unauthenticated_local_remote_and_token_boundaries(monkeypatch):
    clear_security_env(monkeypatch)

    async def get(peer, headers=None, path='/api/tracing/stream?session_id=session-A'):
        transport = httpx.ASGITransport(app=app, client=(peer, 12345))
        async with httpx.AsyncClient(transport=transport, base_url='http://localhost') as client:
            return await client.get(path, headers=headers or {})

    async def run():
        # Local-only access is allowed by existing middleware, but cannot stream.
        response = await get('127.0.0.1')
        assert response.status_code == 503 and 'text/event-stream' not in response.headers['content-type']
        assert (await get('192.0.2.1')).status_code == 401
        monkeypatch.setenv('CAIO_API_TOKEN', TOKEN)
        for peer, headers, expected in [
            ('127.0.0.1', {}, 401),
            ('192.0.2.1', {}, 401),
            ('127.0.0.1', {'Authorization': 'Bearer wrong'}, 401),
            ('192.0.2.1', {'Authorization': f'Bearer {TOKEN}'}, 503),
            ('127.0.0.1', {'Authorization': f'Bearer {TOKEN}'}, 503),
        ]:
            response = await get(peer, headers)
            assert response.status_code == expected
            assert 'text/event-stream' not in response.headers['content-type']
            assert TOKEN not in response.text and 'session-A' not in response.text
            assert all(secret not in response.text for secret in SECRETS)
        # Authentication of other API routes is unchanged.
        assert (await get('127.0.0.1', path='/api/chat/ping')).status_code == 401
        assert (await get('127.0.0.1', {'Authorization': f'Bearer {TOKEN}'}, '/api/chat/ping')).status_code == 200

    asyncio.run(run())
