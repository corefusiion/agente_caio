"""Teste da duração total, sem consulta externa."""
import asyncio

import httpx
import pytest

from caiocore.shopping import SearchError, mercado_livre


def test_total_timeout_fails_closed(monkeypatch):
    async def slow(request):
        await asyncio.sleep(0.2)
        return httpx.Response(200, json={"results": []})

    original = httpx.AsyncClient
    monkeypatch.setattr(mercado_livre, "_TIMEOUT", 0.01)
    monkeypatch.setattr(mercado_livre.httpx, "AsyncClient", lambda **kw: original(transport=httpx.MockTransport(slow), **kw))
    with pytest.raises(SearchError) as exc:
        asyncio.run(mercado_livre.search_offers("teste"))
    assert exc.value.code == "network_error"
