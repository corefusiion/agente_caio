"""Fixtures sintéticas, sem resultados reais ou chamadas de rede."""
import asyncio
import json

import httpx
import pytest

from caiocore.shopping import SearchError, format_result, search_offers
from caiocore.shopping import mercado_livre as ml


def item(n, price, **changes):
    value = {"id": f"MLB{n}", "title": f"Produto fictício {n}", "price": price,
             "currency_id": "BRL", "permalink": f"https://produto.mercadolivre.com.br/MLB-{n}-ficticio",
             "shipping": {"free_shipping": False}}
    value.update(changes)
    return value


def parse(items):
    return ml._parse(json.dumps({"results": items}).encode())


def network(monkeypatch, handler):
    original = httpx.AsyncClient
    def client(**kwargs):
        return original(transport=httpx.MockTransport(handler), **kwargs)
    monkeypatch.setattr(ml.httpx, "AsyncClient", client)


def run(query="produto"):
    return asyncio.run(search_offers(query))


def test_order_top5_dedup_format_and_shipping():
    offers = [item(1, 1000), item(2, 12.5), item(3, 20, shipping={"free_shipping": True}),
              item(4, 3.99), item(5, 14), item(6, 8), item(7, 1),
              item(7, 5), item(8, 2, permalink="https://produto.mercadolivre.com.br/MLB-7-ficticio?tracking=x")]
    result = parse(offers)
    assert [o.price for o in result.offers] == list(map(ml.Decimal, ["1.00", "3.99", "8.00", "12.50", "14.00"]))
    text = format_result(result)
    assert "R$ 12,50" in text and "frete não incluído" in text
    assert "a calcular" in text and "tracking" not in text
    assert "indicado como grátis; confirme no CEP" in format_result(parse([item(3, 20, shipping={"free_shipping": True})]))


@pytest.mark.parametrize("bad_price", [None, "5", True, -1, 0, 1.001, 1000000001])
def test_bad_price_rejected(bad_price):
    assert not parse([item(1, bad_price)]).offers


@pytest.mark.parametrize("url", ["http://produto.mercadolivre.com.br/a", "https://produto.mercadolivre.com.br.evil.com/a",
    "https://mercadolivre.com.br@evil.com/a", "https://user@mercadolivre.com.br/a", "https://mercadolivre.com.br:443/a",
    "https://mercadolivre.com.br/", "https://mercadolivre.com.br\\@evil.com/a", "https://mercadolivre.com.br/a\nspam"])
def test_bad_links_rejected(url):
    assert not parse([item(1, 1, permalink=url)]).offers


def test_bad_fields_and_markdown():
    data = [item(1, 1, title=""), item(2, 2, id="MLB1\n"), item(3, 3, currency_id="USD"),
            item(4, 4, title="x\ninjeção"), item(5, 5, title="[clique](https://evil.test) *teste*", shipping={"free_shipping": "true"})]
    result = parse(data)
    assert result.rejected == 4 and len(result.offers) == 1 and result.offers[0].free_shipping is None
    assert r"\[clique\]\(https://evil.test\) \*teste\*" in format_result(result)


@pytest.mark.parametrize("content", [b'{}', b'{"results":{}}', b'{"results":[],"results":[]}',
    b'{"results":[{"price":NaN}]}', b'bad json', b'\xff', json.dumps({"results": [None] * 51}).encode()])
def test_invalid_payload(content):
    with pytest.raises(SearchError) as exc:
        ml._parse(content)
    assert exc.value.code == "invalid_response"


def test_empty_differs_from_invalid_items():
    assert "Nenhum anúncio encontrado" in format_result(parse([]))
    assert "dados válidos" in format_result(parse([{}]))


def test_success_request_parameters_and_no_credentials(monkeypatch):
    def handler(request):
        assert request.method == "GET" and request.url.host == "api.mercadolibre.com"
        assert dict(request.url.params) == {"q": "fone", "sort": "price_asc", "limit": "50"}
        assert "authorization" not in request.headers and request.headers["accept-encoding"] == "identity"
        return httpx.Response(200, json={"results": [item(2, 10), item(1, 5)]})
    network(monkeypatch, handler)
    assert [o.item_id for o in run(" fone ").offers] == ["MLB1", "MLB2"]


@pytest.mark.parametrize("status,code", [(401, "access_denied"), (403, "access_denied"), (429, "rate_limited"), (500, "http_error"), (302, "http_error")])
def test_http_failures_clean_and_no_redirect(monkeypatch, status, code):
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(status, headers={"location": "https://evil.test/", "content-type": "application/json"}, text="secret remote content")
    network(monkeypatch, handler)
    with pytest.raises(SearchError) as exc:
        run()
    assert exc.value.code == code and "secret" not in str(exc.value) and len(calls) == 1


@pytest.mark.parametrize("headers,body,code", [
    ({"content-type": "text/html"}, b"{}", "invalid_response"),
    ({"content-type": "application/json", "content-encoding": "gzip"}, b"{}", "invalid_response"),
    ({"content-type": "application/json"}, b"x" * (ml._MAX_BYTES + 1), "too_large"),
])
def test_content_restrictions(monkeypatch, headers, body, code):
    network(monkeypatch, lambda request: httpx.Response(200, headers=headers, stream=httpx.ByteStream(body)))
    with pytest.raises(SearchError) as exc:
        run()
    assert exc.value.code == code


def test_network_exception_is_sanitized(monkeypatch):
    def handler(request):
        raise httpx.ConnectError("secret host details", request=request)
    network(monkeypatch, handler)
    with pytest.raises(SearchError) as exc:
        run()
    assert exc.value.code == "network_error" and "secret" not in str(exc.value)


@pytest.mark.parametrize("query", [None, " ", "x" * 121, "a\nb"])
def test_invalid_query_before_network(query, monkeypatch):
    network(monkeypatch, lambda request: pytest.fail("unexpected network request"))
    with pytest.raises(SearchError) as exc:
        run(query)
    assert exc.value.code == "invalid_query"
