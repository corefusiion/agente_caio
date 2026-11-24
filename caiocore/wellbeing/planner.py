"""Pure, offline suggestion evaluator. The trusted caller owns consent and history.

No clock, storage, I/O, scheduling, transport, response collection or inference.
"""
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

UTC = timezone.utc
MESSAGES = {
    "treino": "Se fizer sentido pra você, que tal reservar um momento para se movimentar?",
    "agua": "Se quiser, este pode ser um bom momento para beber água.",
    "mente": "Como você está se sentindo? Não precisa responder.",
    "relacionamento": "Se fizer sentido pra você, que tal reservar um momento para uma conexão importante?",
    "colica_ciclo": "Se quiser, reserve um momento para cuidar de você do jeito que preferir.",
}
# Never substitute a category, sensitive text, name or other metadata here.
PUSH_TITLE = "Um momento para você"
PUSH_BODY = "Você tem uma sugestão de bem-estar."


def _instant(value, name):
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} deve ter fuso horário")
    return value.astimezone(UTC)


def _local_time(value, name):
    if not isinstance(value, time) or value.tzinfo is not None or value.second or value.microsecond or value.fold:
        raise ValueError(f"{name} deve ser um horário local sem fuso, em minutos")


def _integer(value, name, low, high):
    if type(value) is not int or not low <= value <= high:
        raise ValueError(f"{name} fora dos limites")


@dataclass(frozen=True)
class CategoryPreference:
    category: str
    # Both explicit consent and its timestamp are required; default is opt-out.
    consent: bool = False
    consented_at: datetime | None = None
    paused: bool = False
    local_time: time = time(12, 0)
    every_days: int = 1
    anchor_date: date = date(2000, 1, 1)

    def __post_init__(self):
        if not isinstance(self.category, str) or self.category not in MESSAGES:
            raise ValueError("categoria inválida")
        if type(self.consent) is not bool or type(self.paused) is not bool:
            raise ValueError("consentimento e pausa devem ser booleanos")
        if self.consented_at is not None:
            _instant(self.consented_at, "consented_at")
        if self.consent and self.consented_at is None:
            raise ValueError("consentimento exige data explícita")
        _local_time(self.local_time, "local_time")
        _integer(self.every_days, "every_days", 1, 30)
        if type(self.anchor_date) is not date:
            raise ValueError("anchor_date deve ser uma data")


@dataclass(frozen=True)
class Config:
    enabled: bool = False
    paused: bool = False
    paused_until: datetime | None = None
    timezone: str = "UTC"
    quiet_start: time = time(22, 0)
    quiet_end: time = time(7, 0)
    max_per_hour: int = 1
    max_per_day: int = 3
    categories: tuple[CategoryPreference, ...] = field(default_factory=tuple)

    def __post_init__(self):
        if type(self.enabled) is not bool or type(self.paused) is not bool:
            raise ValueError("enabled e paused devem ser booleanos")
        if self.paused_until is not None:
            _instant(self.paused_until, "paused_until")
        try:
            ZoneInfo(self.timezone)
        except (TypeError, ValueError, ZoneInfoNotFoundError) as exc:
            raise ValueError("fuso IANA inválido") from exc
        _local_time(self.quiet_start, "quiet_start")
        _local_time(self.quiet_end, "quiet_end")
        if self.quiet_start == self.quiet_end:
            raise ValueError("início e fim do silêncio devem ser diferentes")
        _integer(self.max_per_hour, "max_per_hour", 1, 2)
        _integer(self.max_per_day, "max_per_day", 1, 5)
        if not isinstance(self.categories, tuple) or not all(isinstance(c, CategoryPreference) for c in self.categories):
            raise ValueError("categories deve ser uma tupla de preferências")
        if len({c.category for c in self.categories}) != len(self.categories):
            raise ValueError("categoria duplicada")


@dataclass(frozen=True)
class Presentation:
    """Confirmed actual presentation only, supplied by the caller for this person."""
    category: str
    at: datetime

    def __post_init__(self):
        if not isinstance(self.category, str) or self.category not in MESSAGES:
            raise ValueError("categoria inválida")
        _instant(self.at, "at")


@dataclass(frozen=True)
class Suggestion:
    # This object is private application data. Only the fixed push fields are safe
    # to use as a push preview, never category or message or identifying metadata.
    category: str
    message: str
    push_title: str = PUSH_TITLE
    push_body: str = PUSH_BODY


def suggest(config: Config, category: str, now: datetime,
            history: tuple[Presentation, ...]) -> Suggestion | None:
    """Return one currently due private suggestion or None. No side effects.

    The caller must supply complete, trustworthy per-person actual presentation
    history, serialize the full check/present/record sequence, and recheck current
    consent immediately before showing anything. Not a delivery authorization.
    """
    if not isinstance(config, Config):
        raise ValueError("config inválida")
    if not isinstance(category, str) or category not in MESSAGES:
        raise ValueError("categoria inválida")
    instant = _instant(now, "now")
    if not isinstance(history, tuple) or not all(isinstance(h, Presentation) for h in history):
        raise ValueError("history deve ser uma tupla de apresentações reais")
    instants = tuple(_instant(h.at, "history.at") for h in history)
    if any(at > instant for at in instants):
        # A clock rollback or corrupted history must not bypass limits.
        return None
    if not config.enabled or config.paused:
        return None
    if config.paused_until is not None and instant < _instant(config.paused_until, "paused_until"):
        return None
    pref = next((c for c in config.categories if c.category == category), None)
    if pref is None or not pref.consent or pref.paused:
        return None
    if instant < _instant(pref.consented_at, "consented_at"):
        return None
    local = instant.astimezone(ZoneInfo(config.timezone))
    local_clock = local.time().replace(second=0, microsecond=0, fold=0)
    start, end = config.quiet_start, config.quiet_end
    if (start <= local_clock < end if start < end else local_clock >= start or local_clock < end):
        return None
    # No backlog: only during the selected local minute, never before anchor.
    days = (local.date() - pref.anchor_date).days
    if days < 0 or days % pref.every_days or local_clock != pref.local_time:
        return None
    if sum(instant - timedelta(hours=1) < at <= instant for at in instants) >= config.max_per_hour:
        return None
    if sum(instant - timedelta(hours=24) < at <= instant for at in instants) >= config.max_per_day:
        return None
    # At most once per local date/category, including repeated DST fold minutes.
    if any(h.category == category and h.at.astimezone(ZoneInfo(config.timezone)).date() == local.date()
           for h in history):
        return None
    return Suggestion(category=category, message=MESSAGES[category])
