"""Trusted-caller domain adapter; deliberately not a delivery or queue service."""
from dataclasses import dataclass, replace
from datetime import datetime
import hashlib
import json
import re

from .priority_engine import Candidate, Decision, NotificationPriority, Preferences, _aware


_SOURCE_ID = re.compile(r"[a-z0-9][a-z0-9._-]{0,63}\Z")


def _check_source(source: str) -> None:
    if not isinstance(source, str) or not _SOURCE_ID.fullmatch(source):
        raise ValueError("source must be a stable lowercase opaque source identifier")


@dataclass(frozen=True)
class SourceConsent:
    """A trusted caller's current, explicit allowlist, scoped to one user."""
    allowed_sources: frozenset[str] = frozenset()

    def __post_init__(self) -> None:
        if not isinstance(self.allowed_sources, frozenset):
            raise ValueError("allowed_sources must be a frozenset")
        for source in self.allowed_sources:
            _check_source(source)


@dataclass(frozen=True)
class SourceCandidate:
    source: str
    candidate: Candidate

    def __post_init__(self) -> None:
        _check_source(self.source)
        if not isinstance(self.candidate, Candidate):
            raise ValueError("candidate must be a Candidate")

    def _namespaced_candidate(self) -> Candidate:
        # Encode both parts without delimiter ambiguity. Do not place source IDs or
        # occurrence keys directly into the engine's database.
        identity = json.dumps([self.source, self.candidate.occurrence_key],
                              ensure_ascii=True, separators=(",", ":")).encode("ascii")
        return replace(self.candidate, occurrence_key=hashlib.sha256(identity).hexdigest())


class NotificationPort:
    """Local evaluation and confirmed-presentation bookkeeping, not atomic delivery.

    Use a separate private data directory for each user. The caller is responsible
    for serializing the whole evaluate/display/record sequence across all workers.
    """

    def __init__(self, data_dir):
        self._engine = NotificationPriority(data_dir)

    def evaluate(self, item: SourceCandidate, preferences: Preferences,
                 consent: SourceConsent, now: datetime) -> Decision:
        if not isinstance(item, SourceCandidate) or not isinstance(preferences, Preferences):
            raise ValueError("expected SourceCandidate and Preferences")
        if not isinstance(consent, SourceConsent):
            raise ValueError("expected SourceConsent")
        _aware(now, "now")
        if item.source not in consent.allowed_sources:
            return Decision(False, "source_opt_out")
        return self._engine.evaluate(item._namespaced_candidate(), preferences, now)

    def record_presented(self, item: SourceCandidate, presented_at: datetime) -> None:
        """Record a *confirmed actual presentation*, even if consent has since changed.

        This records history only. It cannot verify presentation and must not be
        called for queued, attempted, failed, or merely eligible notifications.
        """
        if not isinstance(item, SourceCandidate):
            raise ValueError("expected SourceCandidate")
        self._engine.record_presented(item._namespaced_candidate(), presented_at)
