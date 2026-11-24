"""Offline POST /api/chat integration: real agent/provider, mock HTTP only.

Run: PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 PYTHONDONTWRITEBYTECODE=1 python -m pytest -p no:cacheprovider -q tests/test_opencode_chat_e2e.py
All credentials below are fake; never include them in assertion diagnostics.
"""
import hmac
import json
import socket

import httpx
import pytest
from fastapi.testclient import TestClient

from caiocore.agent.loop import AgentLoop
from caiocore.bus.queue import MessageBus
from caiocore.cli.commands import _make_provider
from caiocore.config.schema import Config
from caiocore.providers.opencode_provider import CHAT_URL, OpenCodeProvider
from caiocore.server import api

_FAKE_PROVIDER_KEY = "fake-opencode-e2e-only-not-a-real-secret"
_FAKE_GATEWAY_TOKEN = "fake-gateway-e2e-only-not-a-real-secret"
_MODEL = "opencode/kimi-k3"


@pytest.fixture
def system(monkeypatch, tmp_path):
    # Block real sockets and DNS, independently of the OpenCode mock transport.
    def network_forbidden(*args, **kwargs):
        raise AssertionError("Network access forbidden by offline test")

    monkeypatch.setattr(socket.socket, "connect", network_forbidden)
    monkeypatch.setattr(socket.socket, "connect_ex", network_forbidden)
    monkeypatch.setattr(socket, "getaddrinfo", network_forbidden)
    monkeypatch.setenv("OPENCODE_API_KEY", _FAKE_PROVIDER_KEY)
    monkeypatch.setenv("CAIO_API_TOKEN", _FAKE_GATEWAY_TOKEN)
    monkeypatch.delenv("CAIO_ALLOWED_ORIGINS", raising=False)
    monkeypatch.delenv("CAIO_ALLOWED_HOSTS", raising=False)

    config = Config()
    config.agents.defaults.model = _MODEL
    config.providers.opencode.enabled = True
    provider = _make_provider(config)
    assert isinstance(provider, OpenCodeProvider)
    assert not (_FAKE_PROVIDER_KEY in config.model_dump_json())

    # Use production registration behavior: OpenCode must remain chat-only.
    agent = AgentLoop(bus=MessageBus(), provider=provider, workspace=tmp_path,
                      config=config, fallback_models=["other/model"])
    agent.status = "ready"  # AgentLoop.run normally sets this when started.
    assert not agent.tools.tool_names
    assert not agent.smart_router.enabled
    monkeypatch.setattr(api, "_agent", agent)

    state = {"mode": "ok", "calls": [], "options": []}
    real_client = httpx.AsyncClient

    def respond(request):
        # Never retain/print the request object or its Authorization header.
        valid_auth = hmac.compare_digest(request.headers.get("authorization", ""),
                                         "Bearer " + _FAKE_PROVIDER_KEY)
        state["calls"].append({"url": str(request.url), "auth_ok": valid_auth,
                               "body": json.loads(request.content)})
        mode = state["mode"]
        if mode == "tools":
            return httpx.Response(200, json={"choices": [{"message": {"content": None, "tool_calls": [{"id": "call-1", "type": "function", "function": {"name": "exec", "arguments": "{}"}}]}, "finish_reason": "tool_calls"}]}, request=request)
        if mode == "connection":
            raise httpx.ConnectError(_FAKE_PROVIDER_KEY, request=request)
        if mode == "malformed":
            return httpx.Response(200, content=b"not-json", request=request)
        if isinstance(mode, int):
            return httpx.Response(mode, json={"error": _FAKE_PROVIDER_KEY},
                                  headers={"location": "https://evil.invalid/"}, request=request)
        return httpx.Response(200, json={"choices": [{"message": {"content": "Resposta simulada"},
                                      "finish_reason": "stop"}]}, request=request)

    def mocked_async_client(**kwargs):
        state["options"].append(kwargs.copy())
        return real_client(**kwargs, transport=httpx.MockTransport(respond))

    monkeypatch.setattr("caiocore.providers.opencode_provider.httpx.AsyncClient", mocked_async_client)
    client = TestClient(api.app, base_url="http://127.0.0.1", client=("127.0.0.1", 12345))
    return client, state, agent, tmp_path


def post(client, message="Olá", session="e2e"):
    return client.post("/api/chat", headers={"Authorization": "Bearer " + _FAKE_GATEWAY_TOKEN},
                       json={"message": message, "session_id": session})


def check_no_leak(text):
    # A boolean assertion avoids printing the secret on failure.
    assert not any(secret in text for secret in (_FAKE_PROVIDER_KEY, _FAKE_GATEWAY_TOKEN))


def test_success_and_session_history(system, capsys):
    client, state, agent, workspace = system
    response = post(client)
    assert response.status_code == 200
    assert response.json()["status"] == "completed"
    assert response.json()["content"] == "Resposta simulada"
    assert response.json()["role"] == "assistant"
    assert len(state["calls"]) == 1
    call = state["calls"][0]
    assert call["url"] == CHAT_URL and call["auth_ok"]
    assert call["body"]["model"] == "kimi-k3"
    assert not call["body"].get("tools") and "tool_choice" not in call["body"]
    assert any(m.get("role") == "user" and m.get("content") == "Olá"
               for m in call["body"]["messages"])
    assert state["options"] == [{"verify": True, "trust_env": False,
                                 "follow_redirects": False, "timeout": 60.0}]
    second = post(client, "Segunda mensagem")
    assert second.status_code == 200 and second.json()["content"] == "Resposta simulada"
    assert len(state["calls"]) == 2
    history = state["calls"][1]["body"]["messages"]
    assert any(m.get("role") == "user" and m.get("content") == "Olá" for m in history)
    assert any(m.get("role") == "assistant" and m.get("content") == "Resposta simulada" for m in history)
    assert any(m.get("role") == "user" and m.get("content") == "Segunda mensagem" for m in history)
    check_no_leak(response.text + second.text)
    captured = capsys.readouterr()
    check_no_leak(captured.out + captured.err)
    for path in workspace.rglob("*"):
        if path.is_file():
            check_no_leak(path.read_text(errors="replace"))


@pytest.mark.parametrize("mode, expected", [(401, "HTTP 401"), (429, "HTTP 429"),
                                            (500, "HTTP 500"), ("malformed", "resposta inválida"),
                                            ("connection", "falha de conexão")])
def test_provider_failure_observed_without_retry_or_leak(system, capsys, mode, expected):
    client, state, _, _ = system
    state["mode"] = mode
    response = post(client)
    assert response.status_code == 200
    assert expected in response.json()["content"]
    assert response.json()["status"] == "error"
    assert len(state["calls"]) == 1
    assert state["calls"][0]["auth_ok"]
    check_no_leak(response.text)
    captured = capsys.readouterr()
    check_no_leak(captured.out + captured.err)


def test_provider_failure_should_not_be_reported_as_completed(system):
    client, state, _, _ = system
    state["mode"] = 500
    response = post(client)
    assert len(state["calls"]) == 1 and "HTTP 500" in response.json()["content"]
    assert response.status_code >= 400 or response.json()["status"] != "completed"


@pytest.mark.parametrize("authorization", [None, "Bearer wrong", "Basic wrong"])
def test_gateway_auth_rejected_before_provider(system, authorization):
    client, state, _, _ = system
    headers = {} if authorization is None else {"Authorization": authorization}
    response = client.post("/api/chat", headers=headers, json={"message": "Olá"})
    assert response.status_code == 401
    assert not state["calls"]
    check_no_leak(response.text)


def test_unsolicited_tool_call_rejected(system):
    client, state, agent, _ = system
    state["mode"] = "tools"
    response = post(client)
    assert response.json()["status"] == "error"
    assert "ferramentas indisponíveis" in response.json()["content"]
    assert not agent.tools.tool_names
    assert len(state["calls"]) == 1


def test_separate_session_history(system):
    client, state, _, _ = system
    assert post(client, "Privado A", "session-a").json()["status"] == "completed"
    assert post(client, "Mensagem B", "session-b").json()["status"] == "completed"
    assert "Privado A" not in json.dumps(state["calls"][-1]["body"])


def test_routing_and_model_commands_cannot_change_opencode(system):
    client, state, agent, _ = system
    post(client, "/routing on")
    post(client, "/model opencode/glm-5.3")
    assert not agent.smart_router.enabled
    assert agent.model == _MODEL
    assert not state["calls"]


def test_settings_cannot_change_provider_or_send_key(system, monkeypatch):
    client, state, agent, _ = system
    monkeypatch.setattr(api, "_config", agent.config)
    headers = {"Authorization": "Bearer " + _FAKE_GATEWAY_TOKEN}
    for change in ({"model": "openai/gpt-4o"}, {"providerKeys": {"opencode": _FAKE_PROVIDER_KEY}},
                   {"providerBases": {"opencode": "https://example.invalid"}},
                   {"providerEnabled": {"opencode": False}}):
        response = client.post("/api/settings", headers=headers, json=change)
        assert response.status_code in (400, 409)
        check_no_leak(response.text)
    assert agent.model == _MODEL and not state["calls"]


def test_initializing_does_not_call_provider(system):
    client, state, agent, _ = system
    agent.status = "initializing"
    response = post(client)
    assert response.status_code == 200 and response.json()["status"] == "initializing"
    assert not state["calls"]
    check_no_leak(response.text)
