"""Pure offline briefing assembly. Inputs and consent come from a trusted caller."""
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import ipaddress
import re
import unicodedata
from urllib.parse import urlsplit
from types import MappingProxyType

UTC = timezone.utc
CATEGORIES = ("weather", "usd_brl", "brazil", "world", "sports", "films")
# Maximum age since BOTH observation/publication and retrieval; not a promise of accuracy.
MAX_AGE = MappingProxyType({"weather": timedelta(hours=3), "usd_brl": timedelta(hours=24),
           "brazil": timedelta(hours=24), "world": timedelta(hours=24),
           "sports": timedelta(hours=24), "films": timedelta(days=7)})
_LABEL = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\Z")
_RESERVED = ("localhost", "local", "internal", "test", "invalid", "example", "onion")


def _host(value):
    if type(value) is not str or value != value.lower() or len(value) > 253:
        raise ValueError("host must be a lowercase ASCII DNS hostname")
    parts = value.split('.')
    if (len(parts) < 2 or any(not _LABEL.fullmatch(p) for p in parts)
            or not re.fullmatch(r"[a-z]{2,63}", parts[-1])
            or parts[-1] in _RESERVED or value in {"example.com", "example.org", "example.net"}):
        raise ValueError("invalid or reserved hostname")
    try:
        ipaddress.ip_address(value)
    except ValueError:
        return value
    raise ValueError("IP addresses are not hostnames")


def _url(value):
    if type(value) is not str or not 1 <= len(value) <= 2048:
        raise ValueError("invalid URL")
    # URL is for attribution only, not fetching. Avoid credentials, query tokens,
    # fragments, parser normalization, and invisible/whitespace characters.
    if any(ord(c) < 33 or ord(c) > 126 or c == '\\' for c in value):
        raise ValueError("invalid URL characters")
    try:
        parsed = urlsplit(value)
        host, port = parsed.hostname, parsed.port
        if (parsed.scheme != 'https' or not host or parsed.username is not None
                or parsed.password is not None or port not in (None, 443)
                or parsed.query or parsed.fragment or '?' in value or '#' in value
                or parsed.netloc not in (host, host + ':443')):
            raise ValueError("invalid HTTPS URL")
        _host(host)
    except (ValueError, TypeError) as exc:
        raise ValueError("invalid HTTPS URL") from exc
    return host


def _instant(value):
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timezone-aware datetime required")
    return value.astimezone(UTC)


def _text(value, limit):
    if (type(value) is not str or value != value.strip() or not 1 <= len(value) <= limit
            or not value.isprintable() or any(unicodedata.category(c).startswith('C') for c in value)):
        raise ValueError("invalid text")
    return value


@dataclass(frozen=True)
class Consent:
    """Current explicit choices from a trusted caller, never an auth mechanism.

    sources holds exact (category, hostname) pairs. Consent defaults to off.
    """
    enabled: bool = False
    categories: frozenset = frozenset()
    sources: frozenset = frozenset()
    use_interests: bool = False

    def __post_init__(self):
        if (type(self.enabled) is not bool or type(self.use_interests) is not bool
                or type(self.categories) is not frozenset or not self.categories <= set(CATEGORIES)
                or type(self.sources) is not frozenset or len(self.sources) > 100):
            raise ValueError("invalid consent")
        for pair in self.sources:
            if type(pair) is not tuple or len(pair) != 2 or pair[0] not in CATEGORIES:
                raise ValueError("invalid source consent")
            _host(pair[1])


@dataclass(frozen=True)
class Item:
    title: str
    text: str
    source_url: str
    observed_at: datetime
    retrieved_at: datetime
    expires_at: datetime  # exclusive bound: recompute before any later use
    # Relevant interest label is included only for sports/films.
    interest: str | None = None


@dataclass(frozen=True)
class Section:
    category: str
    # current, unavailable, or not_consented; invalid/stale data are never emitted.
    status: str
    items: tuple[Item, ...] = ()


@dataclass(frozen=True)
class Briefing:
    generated_at: datetime
    sections: tuple[Section, ...]


def _interests(value, enabled):
    if not enabled:
        return {}
    if type(value) is not dict or not value.keys() <= {'sports', 'films'}:
        raise ValueError("invalid interest map")
    result = {}
    for key, labels in value.items():
        if type(labels) not in (list, tuple) or len(labels) > 40:
            raise ValueError("invalid interests")
        result[key] = frozenset(_text(label, 80).casefold() for label in labels)
    return result


def assemble(records, consent, now, *, interests=None):
    """Build a detached immutable snapshot, without I/O or inferred facts.

    records: list/tuple of dicts, up to 500. Required keys category, title, text,
    source_url, observed_at, retrieved_at; optional interest for sports/films.
    Invalid/expired/future or non-consented records are silently omitted. The
    caller must re-check consent before any later presentation or use.
    """
    now = _instant(now)
    if type(consent) is not Consent:
        raise ValueError("explicit Consent required")
    if type(records) not in (list, tuple) or len(records) > 500:
        raise ValueError("records must be a bounded list or tuple")
    relevant = _interests(interests if interests is not None else {}, consent.enabled and consent.use_interests)
    grouped = {category: [] for category in CATEGORIES}
    if consent.enabled:
        for raw in records:
            if type(raw) is not dict:
                continue
            category = raw.get('category')
            if type(category) is not str or category not in consent.categories:
                continue
            if category in ('sports', 'films') and not relevant.get(category):
                continue
            required = {'category', 'title', 'text', 'source_url', 'observed_at', 'retrieved_at'}
            if not required <= raw.keys() or not raw.keys() <= required | {'interest'}:
                continue
            try:
                host = _url(raw['source_url'])
                if (category, host) not in consent.sources:
                    continue
                observed = _instant(raw['observed_at'])
                retrieved = _instant(raw['retrieved_at'])
                if observed > retrieved or retrieved > now or now - observed >= MAX_AGE[category] or now - retrieved >= MAX_AGE[category]:
                    continue
                title = _text(raw['title'], 200)
                text = _text(raw['text'], 1000)
                interest = raw.get('interest')
                if category in ('sports', 'films'):
                    interest = _text(interest, 80)
                    if interest.casefold() not in relevant[category]:
                        continue
                elif interest is not None:
                    continue
                if len(grouped[category]) < 20:
                    grouped[category].append(Item(title, text, raw['source_url'], observed, retrieved,
                                                  observed + MAX_AGE[category], interest))
            except (ValueError, TypeError, OverflowError):
                continue
    sections = []
    for category in CATEGORIES:
        allowed = (consent.enabled and category in consent.categories and
                   (category not in ('sports', 'films') or consent.use_interests))
        entries = tuple(grouped[category]) if allowed else ()
        sections.append(Section(category, 'not_consented' if not allowed else
                                ('current' if entries else 'unavailable'), entries))
    return Briefing(now, tuple(sections))
