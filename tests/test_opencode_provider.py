"""Offline tests: no real keys, inference calls, DNS, redirects or billing."""
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from caiocore.config.schema import Config
from caiocore.config.loader import save_config
from caiocore.providers.opencode_provider import API_BASE, CHAT_URL, OpenCodeProvider
from caiocore.cli.commands import _make_provider

FAKE_KEY = "fake-test-key-not-a-real-secret"


def configured(model="opencode/kimi-k3", enabled=True):
    config = Config()
    config.agents.defaults.model = model
    config.providers.opencode.enabled = enabled
    return config


def test_selection_opt_in_and_other_provider_not_selected(monkeypatch):
    monkeypatch.setenv("OPENCODE_API_KEY", FAKE_KEY)
    c = configured(enabled=False)
    c.providers.openrouter.api_key = "fake-other"
    assert c.get_provider_name("opencode/kimi-k3:free") == "opencode"
    with pytest.raises(ValueError, match="desativado"):
        _make_provider(c)
    c.providers.opencode.enabled = True
    p = _make_provider(c)
    assert isinstance(p, OpenCodeProvider)
    assert p.get_default_model() == "opencode/kimi-k3"
    assert c.get_provider_name("openrouter/some-model") == "openrouter"
    c.providers.openrouter.api_key = ""
    c.providers.opencode.api_key = "fake-config-key"
    assert c.get_provider_name("unmatched-model") is None
    with pytest.raises(ValueError, match="apenas em OPENCODE_API_KEY"):
        _make_provider(c)


def test_load_config_and_select_from_json(monkeypatch, tmp_path):
    from caiocore.config.loader import load_config
    monkeypatch.setenv("OPENCODE_API_KEY", FAKE_KEY)
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"agents": {"defaults": {"model": "opencode/minimax-m2.7"}}, "providers": {"opencode": {"enabled": True}}}))
    loaded = load_config(path)
    assert isinstance(_make_provider(loaded), OpenCodeProvider)
    assert FAKE_KEY not in loaded.model_dump_json()
    assert FAKE_KEY not in path.read_text()


def test_key_is_environment_only_and_never_serialized(monkeypatch, tmp_path):
    monkeypatch.delenv("OPENCODE_API_KEY", raising=False)
    c = configured()
    with pytest.raises(ValueError, match="OPENCODE_API_KEY"):
        _make_provider(c)
    c.providers.opencode.api_key = FAKE_KEY
    assert FAKE_KEY not in repr(c)
    assert FAKE_KEY not in c.model_dump_json()
    save_config(c, tmp_path / "config.json")
    assert FAKE_KEY not in (tmp_path / "config.json").read_text()
    with pytest.raises(ValueError) as exc:
        _make_provider(c)
    assert FAKE_KEY not in str(exc.value)
    monkeypatch.setenv("OPENCODE_API_KEY", "invalid\r\nAuthorization: injected")
    c.providers.opencode.api_key = ""
    with pytest.raises(ValueError):
        _make_provider(c)


@pytest.mark.parametrize("base", [
    "http://opencode.ai/inference/openai/v1",
    "https://opencode.ai.evil.test/inference/openai/v1",
    "https://opencode.ai@evil.test/inference/openai/v1",
    "https://127.0.0.1/inference/openai/v1",
    "https://opencode.ai:443/inference/openai/v1",
    "https://opencode.ai/inference/openai/v1?redirect=evil",
    "https://opencode.ai/inference/openai/v1/../evil",
    "https://opencode.ai/inference/openai/v1/",
])
def test_ssrf_and_invalid_base_rejected(base):
    with pytest.raises(ValueError):
        OpenCodeProvider(api_key=FAKE_KEY, api_base=base, default_model="opencode/kimi-k3")


@pytest.mark.parametrize("model", ["opencode/gpt-5.4", "opencode/claude-sonnet-5-5", "opencode/gemini-4-argon", "opencode/qwen3.8-flash", "opencode/kimi-k3:free", "opencode/unknown"])
def test_unimplemented_family_and_unverified_model_rejected(model):
    with pytest.raises(ValueError, match="não suportado"):
        OpenCodeProvider(api_key=FAKE_KEY, default_model=model)


@pytest.fixture
def mocked_client(monkeypatch):
    requests = []
    options = []
    reply = {"status": 200, "body": {"choices": [{"message": {"content": "Olá"}, "finish_reason": "stop"}]}}
    real_async_client = httpx.AsyncClient

    def respond(request):
        requests.append(request)
        if reply.get("exception"):
            raise httpx.ConnectError(FAKE_KEY, request=request)
        return httpx.Response(reply["status"], json=reply["body"], headers=reply.get("headers", {}), request=request)

    def client(**kwargs):
        options.append(kwargs.copy())
        return real_async_client(**kwargs, transport=httpx.MockTransport(respond))

    monkeypatch.setattr("caiocore.providers.opencode_provider.httpx.AsyncClient", client)
    return requests, options, reply


@pytest.mark.asyncio
async def test_chat_and_security_transport(mocked_client):
    requests, options, reply = mocked_client
    p = OpenCodeProvider(api_key=FAKE_KEY, api_base=API_BASE, default_model="opencode/kimi-k3")
    r = await p.chat([{"role": "user", "content": "oi"}])
    assert r.content == "Olá" and r.finish_reason == "stop"
    assert len(requests) == 1 and str(requests[0].url) == CHAT_URL
    assert requests[0].headers["authorization"] == "Bearer " + FAKE_KEY
    assert json.loads(requests[0].content)["model"] == "kimi-k3"
    assert options == [{"verify": True, "trust_env": False, "follow_redirects": False, "timeout": 60.0}]
    rejected = await p.chat([], model="openrouter/model")
    assert rejected.finish_reason == "error" and len(requests) == 1


@pytest.mark.asyncio
async def test_structured_tool_call_round_trip(mocked_client):
    requests, _, reply = mocked_client
    reply["body"] = {"choices": [{"finish_reason": "tool_calls", "message": {"content": None,
        "tool_calls": [{"id": "call-1", "type": "function", "function": {"name": "search", "arguments": '{"query":"oi"}'}}]}}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12}}
    p = OpenCodeProvider(api_key=FAKE_KEY, default_model="opencode/glm-5.3")
    tools = [{"type": "function", "function": {"name": "search", "parameters": {"type": "object"}}}]
    result = await p.chat([{"role": "user", "content": "pesquise"}], tools=tools)
    sent = json.loads(requests[0].content)
    assert sent["tools"] == tools and sent["tool_choice"] == "auto"
    assert result.has_tool_calls and result.tool_calls[0].arguments == {"query": "oi"}
    assert result.tool_calls[0].id == "call-1" and result.tool_calls[0].name == "search"
    assert result.usage["total_tokens"] == 12
    reply["body"]["choices"][0]["message"]["tool_calls"][0]["function"]["arguments"] = "bad json"
    assert (await p.chat([], tools=tools)).finish_reason == "error"


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [301, 302, 401, 403, 429, 500])
async def test_http_errors_no_retry_redirect_or_secret_leak(mocked_client, capsys, status):
    requests, _, reply = mocked_client
    reply.update(status=status, body={"error": FAKE_KEY}, headers={"location": "https://evil.test/"})
    p = OpenCodeProvider(api_key=FAKE_KEY, default_model="opencode/kimi-k3")
    result = await p.chat([])
    assert result.finish_reason == "error" and str(status) in result.content
    assert FAKE_KEY not in result.content and FAKE_KEY not in repr(result)
    assert len(requests) == 1 and FAKE_KEY not in capsys.readouterr().out


@pytest.mark.asyncio
async def test_exception_is_redacted_and_no_retry(mocked_client):
    requests, _, reply = mocked_client
    reply["exception"] = True
    p = OpenCodeProvider(api_key=FAKE_KEY, default_model="opencode/kimi-k3")
    result = await p.chat([])
    assert result.finish_reason == "error" and FAKE_KEY not in result.content
    assert len(requests) == 1


@pytest.mark.asyncio
async def test_agent_fallback_disabled_for_opencode():
    from caiocore.agent.loop import AgentLoop
    from caiocore.providers.base import LLMResponse
    fake = SimpleNamespace(disable_automatic_fallback=True, chat=AsyncMock(return_value=LLMResponse(content="error", finish_reason="error")))
    loop = SimpleNamespace(provider=fake, fallback_models=["another"])
    result = await AgentLoop._call_provider_with_fallback(loop, [], None, "opencode/kimi-k3", 100, 0.7)
    assert result.finish_reason == "error" and fake.chat.await_count == 1


def test_agent_smart_routing_disabled(monkeypatch, tmp_path):
    from caiocore.agent.loop import AgentLoop
    from caiocore.bus.queue import MessageBus
    monkeypatch.setattr(AgentLoop, "_register_default_tools", lambda self: None)
    p = OpenCodeProvider(api_key=FAKE_KEY, default_model="opencode/kimi-k3")
    loop = AgentLoop(bus=MessageBus(), provider=p, workspace=tmp_path)
    assert loop.smart_router.enabled is False
    assert loop.smart_router.classify("oi").model == "opencode/kimi-k3"
