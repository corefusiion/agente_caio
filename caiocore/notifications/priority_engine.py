"""Local, opt-in notification *evaluation* only; no polling, AI, delivery or UI.

The trusted caller supplies real candidate metadata and a stable, opaque occurrence
key (not the message text). evaluate() never records a delivery. Only call
record_presented() AFTER the user actually saw the suggestion locally; it stores a
keyed digest and timestamp, never the source content. A caller must serialize the
entire evaluate -> present -> record sequence: this base does not reserve slots.
This module is intentionally not wired into the existing explicit-reminder scheduler.
"""
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta, timezone
import hashlib
import hmac
import os
from pathlib import Path
import sqlite3
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

UTC = timezone.utc
CATEGORIES = frozenset(('appointment', 'conversation', 'interest'))
SENSITIVITIES = {'low': 4, 'medium': 3, 'high': 2}
URGENCY_POINTS = {'normal': 0, 'high': 1, 'critical': 2}


def _aware(value, name):
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f'{name} must be a timezone-aware datetime')
    return value.astimezone(UTC)


def _micros(value):
    delta = _aware(value, 'time') - datetime(1970, 1, 1, tzinfo=UTC)
    return ((delta.days * 86400 + delta.seconds) * 1000000 + delta.microseconds)


@dataclass(frozen=True)
class Preferences:
    # Requires explicit overall and per-category consent; no source is accessed here.
    enabled: bool = False
    categories: frozenset = field(default_factory=frozenset)
    sensitivity: str = 'medium'
    timezone: str = 'America/Bahia'
    quiet_start: time = time(22, 0)
    quiet_end: time = time(7, 0)
    max_per_hour: int = 3
    max_per_day: int = 8  # rolling 24 hours, not calendar day
    dedup_hours: int = 24

    def __post_init__(self):
        if type(self.enabled) is not bool or not isinstance(self.categories, frozenset) or not self.categories <= CATEGORIES:
            raise ValueError('Invalid consent settings')
        if self.sensitivity not in SENSITIVITIES:
            raise ValueError('Invalid sensitivity')
        try:
            ZoneInfo(self.timezone)
        except (ValueError, TypeError, ZoneInfoNotFoundError) as exc:
            raise ValueError('Invalid IANA timezone') from exc
        if (not isinstance(self.quiet_start, time) or not isinstance(self.quiet_end, time)
                or self.quiet_start.tzinfo is not None or self.quiet_end.tzinfo is not None
                or self.quiet_start == self.quiet_end):
            raise ValueError('Invalid quiet hours; start and end must differ')
        for name, upper in (('max_per_hour', 100), ('max_per_day', 1000), ('dedup_hours', 720)):
            value = getattr(self, name)
            if type(value) is not int or not 1 <= value <= upper:
                raise ValueError(f'Invalid {name}')


@dataclass(frozen=True)
class Candidate:
    category: str
    occurrence_key: str  # stable, non-content ID; include occurrence for recurring events
    observed_at: datetime
    relevance: int  # explicit caller-provided rating: 0, 1, 2; not inferred by AI
    urgency: str = 'normal'  # explicit caller-provided classification
    starts_at: datetime = None  # required for appointment

    def __post_init__(self):
        if self.category not in CATEGORIES or not isinstance(self.occurrence_key, str) or not 1 <= len(self.occurrence_key) <= 256:
            raise ValueError('Invalid candidate identity or category')
        if type(self.relevance) is not int or self.relevance not in (0, 1, 2) or self.urgency not in URGENCY_POINTS:
            raise ValueError('Invalid relevance or urgency')
        _aware(self.observed_at, 'observed_at')
        if self.category == 'appointment' and self.starts_at is None:
            raise ValueError('Appointment requires starts_at')
        if self.starts_at is not None:
            _aware(self.starts_at, 'starts_at')


@dataclass(frozen=True)
class Decision:
    eligible: bool
    reason: str
    score: int = 0


class NotificationPriority:
    def __init__(self, data_dir):
        directory = Path(data_dir)
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.path = directory / 'notification_priority.sqlite3'
        with self._connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS priority_meta (id INTEGER PRIMARY KEY CHECK(id=1), secret BLOB NOT NULL)')
            db.execute('INSERT OR IGNORE INTO priority_meta(id, secret) VALUES(1, ?)', (os.urandom(32),))
            db.execute('CREATE TABLE IF NOT EXISTS presented (digest BLOB NOT NULL, at_us INTEGER NOT NULL)')
            db.execute('CREATE INDEX IF NOT EXISTS presented_time ON presented(at_us)')
            db.execute('CREATE INDEX IF NOT EXISTS presented_digest_time ON presented(digest, at_us)')
        try:
            os.chmod(self.path, 0o600)
        except OSError:
            pass  # Windows file access follows the user's directory ACLs

    @contextmanager
    def _connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        try:
            with db:
                yield db
        finally:
            db.close()

    @staticmethod
    def _digest(db, candidate):
        secret = db.execute('SELECT secret FROM priority_meta WHERE id=1').fetchone()[0]
        identity = (candidate.category + '\0' + candidate.occurrence_key).encode('utf-8')
        return hmac.new(secret, identity, hashlib.sha256).digest()

    def evaluate(self, candidate, preferences, now):
        """Read-only decision; never changes history, delivers or schedules anything.

        Quiet hours are [start,end), applied in the configured IANA local timezone,
        including both sides of DST folds. All caps and dedup are rolling real time.
        Inputs and decisions are local and entirely deterministic.
        """
        if not isinstance(candidate, Candidate) or not isinstance(preferences, Preferences):
            raise ValueError('Expected Candidate and Preferences')
        now = _aware(now, 'now')
        if not preferences.enabled or candidate.category not in preferences.categories:
            return Decision(False, 'opt_out')
        local_time = now.astimezone(ZoneInfo(preferences.timezone)).time()
        start, end = preferences.quiet_start, preferences.quiet_end
        quiet = start <= local_time < end if start < end else local_time >= start or local_time < end
        if quiet:
            return Decision(False, 'quiet_hours')
        observed = _aware(candidate.observed_at, 'observed_at')
        if observed > now or now - observed > timedelta(days=7):
            return Decision(False, 'stale_or_future')
        points = candidate.relevance + URGENCY_POINTS[candidate.urgency]
        if candidate.category == 'appointment':
            until = _aware(candidate.starts_at, 'starts_at') - now
            if until < timedelta(0) or until > timedelta(days=7):
                return Decision(False, 'appointment_outside_window')
            if until <= timedelta(hours=1):
                points += 2
            elif until <= timedelta(hours=24):
                points += 1
        if points < SENSITIVITIES[preferences.sensitivity]:
            return Decision(False, 'below_threshold', points)
        now_us = _micros(now)
        hour_us = 3600 * 1000000
        with self._connect() as db:
            # A wall-clock rollback must not bypass caps or dedup.
            if db.execute('SELECT 1 FROM presented WHERE at_us > ? LIMIT 1', (now_us,)).fetchone():
                return Decision(False, 'clock_rollback', points)
            digest = self._digest(db, candidate)
            if db.execute('SELECT 1 FROM presented WHERE digest=? AND at_us>? AND at_us<=? LIMIT 1',
                          (digest, now_us - preferences.dedup_hours * hour_us, now_us)).fetchone():
                return Decision(False, 'duplicate', points)
            hour_count = db.execute('SELECT COUNT(*) FROM presented WHERE at_us>? AND at_us<=?', (now_us - hour_us, now_us)).fetchone()[0]
            day_count = db.execute('SELECT COUNT(*) FROM presented WHERE at_us>? AND at_us<=?', (now_us - 24 * hour_us, now_us)).fetchone()[0]
        if hour_count >= preferences.max_per_hour:
            return Decision(False, 'hour_limit', points)
        if day_count >= preferences.max_per_day:
            return Decision(False, 'day_limit', points)
        return Decision(True, 'eligible', points)

    def record_presented(self, candidate, presented_at):
        """Record an actual local presentation, never a mere evaluation or attempt.

        This is a trusted-caller contract, not proof of display. All actual displays
        should be recorded even if preferences changed after the decision. The caller
        must serialize evaluation, presentation and recording to enforce caps.
        """
        if not isinstance(candidate, Candidate):
            raise ValueError('Expected Candidate')
        instant = _micros(presented_at)
        with self._connect() as db:
            db.execute('INSERT INTO presented(digest, at_us) VALUES(?, ?)', (self._digest(db, candidate), instant))
