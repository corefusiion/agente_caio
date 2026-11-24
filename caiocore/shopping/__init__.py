"""Pesquisa de ofertas, isolada do agente e sem efeitos de compra ou postagem."""

from .mercado_livre import Offer, SearchError, SearchResult, format_result, search_offers

__all__ = ["Offer", "SearchError", "SearchResult", "format_result", "search_offers"]
