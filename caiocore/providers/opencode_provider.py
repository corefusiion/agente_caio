"""OpenCode Console's native OpenAI Chat Completions models, opt-in only.

No proxy, redirects, retries or configurable destination: the bearer token must
only go to the official HTTPS endpoint. Other native API families are rejected.
"""
from __future__ import annotations

import json
import re
from typing import Any

import httpx

from caiocore.providers.base import LLMProvider, LLMResponse, ToolCallRequest

API_BASE = "https://opencode.ai/inference/openai/v1"
CHAT_URL = API_BASE + "/chat/completions"
# Snapshot from https://opencode.ai/docs/console/models/ (native endpoint).
# Other models/families intentionally fail closed until reviewed.
CHAT_MODELS = frozenset({
    "kimi-k2.5", "kimi-k2.6", "kimi-k2.7-code", "kimi-k3", "kimi-k3.1-pro",
    "glm-5.3", "glm-5.3-flash", "minimax-m3", "minimax-m2.7", "minimax-m2.5",
    "deepseek-v4.1-flash", "deepseek-v4-pro", "qwen3.8-max", "mistral-large-4",
})


def _model_id(model: str) -> str:
    if model.startswith("opencode/"):
        model = model[len("opencode/"):]
    if model not in CHAT_MODELS:
        raise ValueError("Modelo OpenCode não suportado: apenas modelos nativos Chat Completions da lista validada.")
    return model


class OpenCodeProvider(LLMProvider):
    # AgentLoop's default routing/fallback can silently change models, retry a
    # paid request, or pass an unrelated model to this provider.
    disable_smart_routing = True
    disable_automatic_fallback = True
    disable_agent_tools = True

    def __init__(self, *, api_key: str, default_model: str, api_base: str | None = None):
        super().__init__(api_key=None, api_base=API_BASE)
        if api_base is not None and api_base != API_BASE:
            raise ValueError("Endpoint OpenCode inválido: somente a URL HTTPS oficial é permitida.")
        if not api_key or not re.fullmatch(r"[\x21-\x7e]+", api_key):
            raise ValueError("Defina uma OPENCODE_API_KEY válida no ambiente.")
        self._model = _model_id(default_model)
        self.default_model = "opencode/" + self._model
        self._api_key = api_key

    def get_default_model(self) -> str:
        return self.default_model

    async def chat(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None = None,
                   model: str | None = None, max_tokens: int = 4096,
                   temperature: float = 0.7) -> LLMResponse:
        try:
            selected = _model_id(model or self.default_model)
            if selected != self._model:
                raise ValueError("Modelo diferente do selecionado.")
        except (ValueError, AttributeError, TypeError):
            return LLMResponse(content="OpenCode: modelo não suportado ou diferente do selecionado.", finish_reason="error")

        payload: dict[str, Any] = {
            "model": selected, "messages": messages,
            "max_tokens": max(1, max_tokens), "temperature": temperature,
        }
        if tools:
            payload.update(tools=tools, tool_choice="auto")
        try:
            # trust_env=False excludes proxy and certificate overrides. Redirects
            # are explicitly refused; no SDK retry or TLS downgrade exists.
            async with httpx.AsyncClient(verify=True, trust_env=False, follow_redirects=False, timeout=60.0) as client:
                response = await client.post(CHAT_URL, headers={"Authorization": "Bearer " + self._api_key}, json=payload)
            if not 200 <= response.status_code < 300:
                return LLMResponse(content=f"OpenCode: erro HTTP {response.status_code}.", finish_reason="error")
            return self._parse(response.json())
        except Exception:
            # Never include exception/request/response text: any may contain a key.
            return LLMResponse(content="OpenCode: falha de conexão ou resposta inválida.", finish_reason="error")

    @staticmethod
    def _parse(data: Any) -> LLMResponse:
        choice = data["choices"][0]
        message = choice["message"]
        content = message.get("content")
        if content is not None and not isinstance(content, str):
            raise ValueError("invalid content")
        calls = []
        for call in message.get("tool_calls") or []:
            if call.get("type") != "function":
                raise ValueError("unsupported tool type")
            function = call["function"]
            args = json.loads(function["arguments"])
            if not isinstance(args, dict) or not isinstance(call["id"], str) or not isinstance(function["name"], str):
                raise ValueError("invalid tool call")
            calls.append(ToolCallRequest(id=call["id"], name=function["name"], arguments=args))
        raw_usage = data.get("usage") or {}
        usage = {k: raw_usage[k] for k in ("prompt_tokens", "completion_tokens", "total_tokens")
                 if isinstance(raw_usage.get(k), int)}
        return LLMResponse(content=content, tool_calls=calls,
                           finish_reason=choice.get("finish_reason") or "stop", usage=usage,
                           reasoning_content=message.get("reasoning_content"))
