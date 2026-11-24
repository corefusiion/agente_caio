"""Offline-only prototype for per-task AI-text approval. NOT wired to the queue.

There is intentionally no production execution path yet: endpoint/recipient and
consent provenance cannot be verified from the current queue and providers. This
module never instantiates a configured provider. See docs/TAREFAS-IA-PENDENCIAS.md.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import math
from dataclasses import dataclass
from urllib.parse import urlsplit

from caiocore.providers.base import LLMResponse


class Disabled(RuntimeError):
    pass


class InvalidResult(RuntimeError):
    pass


def _plain(value: str, max_length: int) -> str:
    if type(value) is not str or not value.strip() or len(value) > max_length:
        raise ValueError('invalid text')
    if any((ord(c) < 32 and c not in '\n\t') or 0x7f <= ord(c) <= 0x9f for c in value):
        raise ValueError('invalid text')
    try:
        value.encode('utf-8')
    except UnicodeEncodeError:
        raise ValueError('invalid text') from None
    return value


@dataclass(frozen=True)
class Disclosure:
    """A proposed disclosure, NOT proof of the actual provider routing."""
    task_id: str
    provider: str
    endpoint: str
    model: str
    text: str

    def fingerprint(self) -> str:
        encoded = json.dumps(vars(self), sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
        return hashlib.sha256(encoded).hexdigest()


def prepare(task_id: str, provider: str, endpoint: str, model: str, text: str) -> Disclosure:
    task_id = _plain(task_id, 64)
    provider = _plain(provider, 100)
    endpoint = _plain(endpoint, 500)
    model = _plain(model, 200)
    text = _plain(text, 4000)
    url = urlsplit(endpoint)
    if (url.scheme != 'https' or not url.hostname or url.username or url.password
            or url.query or url.fragment or any(c.isspace() for c in endpoint)):
        raise ValueError('endpoint must be HTTPS without credentials, query or fragment')
    return Disclosure(task_id, provider, endpoint, model, text)


@dataclass(frozen=True)
class TestApproval:
    """In-memory demonstration only; not authenticated consent evidence."""
    fingerprint: str


def approve_for_test(disclosure: Disclosure) -> TestApproval:
    return TestApproval(disclosure.fingerprint())


class FakeProvider:
    """Offline fake; no network, subprocess, threads or files."""
    def __init__(self, response: LLMResponse, delay: float = 0):
        self.response = response
        self.delay = delay
        self.calls = []

    async def chat(self, *, messages, tools, model, max_tokens, temperature):
        self.calls.append((messages, tools, model, max_tokens, temperature))
        await asyncio.sleep(self.delay)
        return self.response


async def _invoke(provider, disclosure: Disclosure, timeout: float) -> str:
    """Prototype provider call shape. Do not call directly in production."""
    async with asyncio.timeout(timeout):
        response = await provider.chat(
            messages=[{'role': 'user', 'content': disclosure.text}],
            tools=None, model=disclosure.model, max_tokens=512, temperature=0.2,
        )
    # Never persist provider error strings, tool calls, or partial completions.
    if (not isinstance(response, LLMResponse) or response.tool_calls
            or response.finish_reason != 'stop'):
        raise InvalidResult('invalid provider response')
    try:
        return _plain(response.content, 2000)
    except ValueError:
        raise InvalidResult('invalid provider response') from None


async def exercise_offline(disclosure: Disclosure, approval: TestApproval, provider: FakeProvider, *, timeout: float = 10) -> str:
    """Only runs the exact local fake, not real or user-supplied providers."""
    if type(provider) is not FakeProvider or type(disclosure) is not Disclosure or type(approval) is not TestApproval:
        raise ValueError('offline test only')
    if approval.fingerprint != disclosure.fingerprint():
        raise ValueError('approval does not match disclosure')
    if type(timeout) not in (float, int) or not math.isfinite(timeout) or not 0 < timeout <= 60:
        raise ValueError('invalid timeout')
    return await _invoke(provider, disclosure, timeout)


async def execute_real(*args, **kwargs):
    """Fail closed; no queue task or flag can turn this into a network request."""
    raise Disabled('Production AI tasks disabled: routing and per-task consent are not verified')
