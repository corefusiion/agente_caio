"""Busca somente-leitura; não integra o registry nem executa ações externas.

O endpoint de busca por texto deve ser validado com a aplicação autorizada antes
 de habilitar no agente. Os dados recebidos são conteúdo não confiável.
"""
from __future__ import annotations

import asyncio
import json
import re
import unicodedata
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from urllib.parse import urlsplit, urlunsplit

import httpx

_ENDPOINT = "https://api.mercadolibre.com/sites/MLB/search"
_ALLOWED_HOSTS = frozenset({"mercadolivre.com.br", "www.mercadolivre.com.br", "produto.mercadolivre.com.br"})
_MAX_BYTES = 512 * 1024
_MAX_ITEMS = 50
_TIMEOUT = 12.0
_ITEM_ID = re.compile(r"MLB[0-9]{1,20}\Z")


class SearchError(Exception):
    """Erro seguro para apresentação, sem corpo de resposta ou credenciais."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class Offer:
    item_id: str
    title: str
    price: Decimal
    url: str
    free_shipping: bool | None


@dataclass(frozen=True, slots=True)
class SearchResult:
    offers: tuple[Offer, ...]
    received: int
    rejected: int


def _query(value: str) -> str:
    if not isinstance(value, str):
        raise SearchError("invalid_query", "Informe o produto como texto.")
    value = value.strip()
    if not value or len(value) > 120 or any(unicodedata.category(c).startswith("C") for c in value):
        raise SearchError("invalid_query", "Informe um produto de 1 a 120 caracteres, sem caracteres de controle.")
    return value


def _url(value: object) -> str | None:
    if not isinstance(value, str) or not 1 <= len(value) <= 2048:
        return None
    if any(ord(c) < 33 or ord(c) > 126 for c in value) or "\\" in value:
        return None
    try:
        parts = urlsplit(value)
        if (parts.scheme != "https" or parts.hostname not in _ALLOWED_HOSTS
                or parts.username is not None or parts.password is not None
                or parts.port is not None or not parts.path.startswith("/")
                or parts.path == "/"):
            return None
        # Remover rastreamento e fragmentos, sem alterar o caminho do anúncio.
        return urlunsplit(("https", parts.hostname, parts.path, "", ""))
    except ValueError:
        return None


def _price(value: object) -> Decimal | None:
    if isinstance(value, bool) or not isinstance(value, (int, Decimal)):
        return None
    try:
        amount = Decimal(value)
        if not amount.is_finite() or amount <= 0 or amount > Decimal("1000000000"):
            return None
        if amount != amount.quantize(Decimal("0.01")):
            return None
        return amount.quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError):
        return None


def _offer(item: object) -> Offer | None:
    if not isinstance(item, dict):
        return None
    ident, title = item.get("id"), item.get("title")
    price, url = _price(item.get("price")), _url(item.get("permalink"))
    if (not isinstance(ident, str) or not _ITEM_ID.fullmatch(ident)
            or not isinstance(title, str) or not title.strip() or len(title) > 300
            or any(unicodedata.category(c).startswith("C") for c in title)
            or item.get("currency_id") != "BRL" or price is None or url is None):
        return None
    shipping = item.get("shipping")
    free = shipping.get("free_shipping") if isinstance(shipping, dict) else None
    if type(free) is not bool:
        free = None
    return Offer(ident, title.strip(), price, url, free)


def _unique_object(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate key")
        result[key] = value
    return result


def _parse(data: bytes) -> SearchResult:
    try:
        payload = json.loads(data, parse_float=Decimal, parse_int=int,
                             parse_constant=lambda _: (_ for _ in ()).throw(ValueError("invalid number")),
                             object_pairs_hook=_unique_object)
    except (ValueError, UnicodeDecodeError, RecursionError):
        raise SearchError("invalid_response", "A resposta do Mercado Livre é inválida.") from None
    if not isinstance(payload, dict) or not isinstance(payload.get("results"), list):
        raise SearchError("invalid_response", "A resposta do Mercado Livre não contém uma lista de resultados válida.")
    items = payload["results"]
    if len(items) > _MAX_ITEMS:
        raise SearchError("invalid_response", "A resposta do Mercado Livre excedeu o limite de resultados.")
    accepted = []
    for item in items:
        offer = _offer(item)
        if offer is not None:
            accepted.append(offer)
    accepted.sort(key=lambda offer: (offer.price, offer.item_id, offer.url))
    seen_ids: set[str] = set()
    seen_urls: set[str] = set()
    unique = []
    for offer in accepted:
        if offer.item_id in seen_ids or offer.url in seen_urls:
            continue
        seen_ids.add(offer.item_id)
        seen_urls.add(offer.url)
        unique.append(offer)
    return SearchResult(tuple(unique[:5]), len(items), len(items) - len(accepted))


async def search_offers(query: str) -> SearchResult:
    """Busca até 50 resultados em uma página e retorna até 5 menores preços.

    Nenhum token é coletado ou usado. Caso a API exija autenticação, a função
    falha sem tentar contornar o bloqueio; integração autenticada é futura.
    """
    query = _query(query)
    try:
        async with asyncio.timeout(_TIMEOUT):
            async with httpx.AsyncClient(timeout=httpx.Timeout(8.0), follow_redirects=False) as client:
                async with client.stream("GET", _ENDPOINT,
                    params={"q": query, "sort": "price_asc", "limit": str(_MAX_ITEMS)},
                    headers={"Accept": "application/json", "Accept-Encoding": "identity"}) as response:
                    status = response.status_code
                    if status in (401, 403):
                        raise SearchError("access_denied", "A API negou acesso à busca; autorização ou permissões podem ser necessárias.")
                    if status == 429:
                        raise SearchError("rate_limited", "A API limitou as consultas; tente mais tarde.")
                    if status != 200:
                        raise SearchError("http_error", f"A API respondeu com HTTP {status}; busca indisponível.")
                    encoding = response.headers.get("content-encoding", "identity").strip().lower()
                    if encoding != "identity":
                        raise SearchError("invalid_response", "A API retornou compressão não solicitada.")
                    content_type = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
                    if content_type != "application/json":
                        raise SearchError("invalid_response", "A API não retornou JSON.")
                    content = bytearray()
                    async for chunk in response.aiter_bytes(chunk_size=16384):
                        content.extend(chunk)
                        if len(content) > _MAX_BYTES:
                            raise SearchError("too_large", "A resposta da API excedeu o limite permitido.")
                    return _parse(bytes(content))
    except SearchError:
        raise
    except (httpx.HTTPError, TimeoutError, OSError):
        raise SearchError("network_error", "Não foi possível consultar a API do Mercado Livre.") from None


def _escape_title(title: str) -> str:
    # Dados da API são conteúdo, nunca instruções, e não devem criar links.
    return re.sub(r"([\\`*_{}\[\]()<>!#|~])", r"\\\1", title)


def _brl(amount: Decimal) -> str:
    whole, cents = f"{amount:.2f}".split(".")
    grouped = f"{int(whole):,}".replace(",", ".")
    return f"R$ {grouped},{cents}"


def format_result(result: SearchResult) -> str:
    if not result.offers:
        if result.received:
            return "Nenhum anúncio recebido tinha dados válidos suficientes. Tente novamente ou refine a busca."
        return "Nenhum anúncio encontrado. Tente uma busca mais ampla."
    lines = ["🛍️ Ofertas no Mercado Livre", "Menores preços dos produtos entre os anúncios recebidos; frete não incluído na ordenação.", ""]
    for number, offer in enumerate(result.offers, 1):
        shipping = ("indicado como grátis; confirme no CEP" if offer.free_shipping is True
                    else "a calcular no site para seu CEP")
        lines.extend([f"{number}. *{_escape_title(offer.title)}*", f"💰 {_brl(offer.price)}",
                      f"🚚 Frete: {shipping}", f"🔗 {offer.url}", ""])
    lines.append("Preços e disponibilidade podem mudar. Confira o valor final no Mercado Livre.")
    return "\n".join(lines)
