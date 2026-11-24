import asyncio
import sys
import types
from types import SimpleNamespace as NS

import pytest
from fastapi import FastAPI, WebSocket
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from caiocore.server import api
from caiocore.server.security import SecurityMiddleware


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for key in ("CAIO_API_TOKEN", "CAIO_ALLOWED_HOSTS", "CAIO_ALLOWED_ORIGINS", "CAIO_EVOLUTION_WEBHOOK_TOKEN"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(api, "_agent", None)
    monkeypatch.setattr(api, "_channels", None)


def client(peer="127.0.0.1", base="http://localhost"):
    return TestClient(api.app, base_url=base, client=(peer, 12345))


def test_local_same_origin_and_denials():
    c = client()
    assert c.get('/api/chat/ping').status_code == 200
    assert c.post('/api/chat', json={}, headers={'origin': 'http://localhost'}).status_code == 503
    assert c.post('/api/chat', json={}).status_code == 403
    assert c.get('/api/chat/ping', headers={'host': 'evil.example'}).status_code == 403
    assert c.get('/api/chat/ping', headers={'origin': 'http://evil.example'}).status_code == 403
    assert c.get('/api/chat/ping', headers={'origin': 'null'}).status_code == 403
    assert c.get('/api/chat/ping', headers={'x-forwarded-for': '127.0.0.1'}).status_code == 401
    assert client('192.0.2.5').get('/api/chat/ping').status_code == 401
    assert 'access-control-allow-origin' not in c.get('/api/chat/ping').headers


def test_external_requires_token_and_explicit_host(monkeypatch):
    c = client('192.0.2.5', 'https://caio.example')
    assert c.get('/api/chat/ping').status_code == 403
    monkeypatch.setenv('CAIO_ALLOWED_HOSTS', 'caio.example')
    assert c.get('/api/chat/ping').status_code == 401
    monkeypatch.setenv('CAIO_API_TOKEN', 'api-test-secret')
    assert c.get('/api/chat/ping', headers={'authorization': 'Bearer wrong'}).status_code == 401
    assert c.get('/api/chat/ping', headers={'authorization': 'Bearer api-test-secret'}).status_code == 200
    assert c.post('/api/chat', json={}, headers={'authorization': 'Bearer api-test-secret'}).status_code == 503
    assert c.post('/api/chat', json={}, headers={'origin': 'https://evil.example', 'authorization': 'Bearer api-test-secret'}).status_code == 403
    assert client().get('/api/chat/ping').status_code == 401


def test_explicit_dev_origin(monkeypatch):
    c = client()
    h = {'origin': 'http://localhost:5177'}
    assert c.post('/api/chat', json={}, headers=h).status_code == 403
    monkeypatch.setenv('CAIO_ALLOWED_ORIGINS', 'http://localhost:5177')
    assert c.post('/api/chat', json={}, headers=h).status_code == 503
    assert c.post('/api/chat', json={}, headers={'origin': 'http://localhost:5178'}).status_code == 403


def test_webhook_fails_closed_before_dispatch(monkeypatch):
    received = []
    class Channel:
        async def handle_webhook(self, data):
            received.append(data)
    monkeypatch.setattr(api, '_channels', NS(get_channel=lambda name: Channel()))
    c = client('192.0.2.7')
    path = '/api/webhooks/evolution'
    assert c.post(path, json={'event': 'bad'}).status_code == 403
    monkeypatch.setenv('CAIO_EVOLUTION_WEBHOOK_TOKEN', 'webhook-test-secret')
    assert c.post(path, json={'event': 'bad'}, headers={'x-evolution-webhook-token': 'wrong'}).status_code == 403
    assert not received
    # Use a context-managed TestClient so background tasks complete before shutdown.
    with client('192.0.2.7') as c:
        assert c.post(path, json={'event': 'ok'}, headers={'x-evolution-webhook-token': 'webhook-test-secret'}).json() == {'status': 'ok'}
        for _ in range(20):
            if received: break
            import time; time.sleep(.01)
    assert received == [{'event': 'ok'}]


def test_websocket_origin_and_network(monkeypatch):
    wsapp = FastAPI()
    @wsapp.websocket('/ws')
    async def ws(socket: WebSocket):
        await socket.accept()
        await socket.send_text('ok')
        await socket.close()
    wsapp.add_middleware(SecurityMiddleware)
    c = TestClient(wsapp, base_url='http://localhost', client=('127.0.0.1', 12345))
    with c.websocket_connect('ws://localhost/ws', headers={'origin': 'http://localhost'}) as s:
        assert s.receive_text() == 'ok'
    for headers in ({}, {'origin': 'http://evil.example'}, {'origin': 'http://localhost', 'host': 'evil.example'}):
        with pytest.raises(WebSocketDisconnect):
            with c.websocket_connect('ws://localhost/ws', headers=headers): pass
    remote = TestClient(wsapp, base_url='http://localhost', client=('192.0.2.2', 12345))
    with pytest.raises(WebSocketDisconnect):
        with remote.websocket_connect('ws://localhost/ws', headers={'origin': 'http://localhost'}): pass
    monkeypatch.setenv('CAIO_API_TOKEN', 'api-test-secret')
    with remote.websocket_connect('ws://localhost/ws', headers={'origin': 'http://localhost', 'authorization': 'Bearer api-test-secret'}) as s:
        assert s.receive_text() == 'ok'


def test_settings_redact_and_preserve(monkeypatch):
    search = NS(provider='brave', api_key='brave-original', tavily_key='tavily-original')
    providers = NS(**{p: NS(api_key=f'{p}-original', api_base=None, enabled=True, extra_headers={}) for p in ('openrouter', 'gemini', 'groq', 'openai', 'anthropic', 'deepseek', 'custom', 'omniroute')})
    cfg = NS(agents=NS(defaults=NS(model='model', max_tokens=4096, temperature=.7, autonomous=True)),
        channels=NS(telegram=NS(enabled=False, token='telegram-original', allow_from=[], notify_chat_id=''),
                    email=NS(enabled=False, imap_password='email-original', smtp_password='email-original', imap_username='', smtp_host='', smtp_port=587),
                    evolution=NS(enabled=False, api_key='evo-original', base_url='', instance_name='')),
        providers=providers, tools=NS(web=NS(search=search)))
    monkeypatch.setattr(api, '_config', cfg)
    fake_loader = types.ModuleType('caiocore.config.loader')
    fake_loader.save_config = lambda config: None
    monkeypatch.setitem(sys.modules, 'caiocore.config.loader', fake_loader)
    c = client()
    cfg.channels.evolution.base_url = 'https://user:password@evolution.example/?key=url-secret'
    cfg.providers.omniroute.api_base = 'https://api.example/?api_key=url-secret'
    response = c.get('/api/settings')
    assert response.status_code == 200
    assert '-original' not in response.text
    assert 'url-secret' not in response.text and 'password@' not in response.text
    result = response.json()
    assert result['telegramToken'] == result['emailPass'] == result['evolutionApiKey'] == '********'
    assert result['providerKeys']['openai'] == result['braveKey'] == result['searchEngineId'] == '********'
    payload = {'telegramToken': '', 'emailPass': '********', 'evolutionApiKey': '••••', 'braveKey': ' ', 'tavilyKey': '[redacted]', 'providerKeys': {'openai': '********', 'groq': '', 'gemini': 'new-gemini'}, 'evolutionBaseUrl': '********', 'providerBases': {'omniroute': '********'}}
    assert c.post('/api/settings', json=payload, headers={'origin': 'http://localhost'}).status_code == 200
    assert cfg.channels.telegram.token == 'telegram-original'
    assert cfg.channels.email.imap_password == cfg.channels.email.smtp_password == 'email-original'
    assert cfg.channels.evolution.api_key == 'evo-original'
    assert search.api_key == 'brave-original' and search.tavily_key == 'tavily-original'
    assert providers.openai.api_key == 'openai-original' and providers.groq.api_key == 'groq-original'
    assert providers.gemini.api_key == 'new-gemini'
    assert cfg.channels.evolution.base_url.endswith('?key=url-secret')
    assert cfg.providers.omniroute.api_base.endswith('?api_key=url-secret')
