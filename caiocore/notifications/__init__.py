"""Isolated notification evaluation; nothing is started on import."""

from .port import NotificationPort, SourceCandidate, SourceConsent
from .priority_engine import Candidate, Decision, Preferences

__all__ = ["Candidate", "Decision", "NotificationPort", "Preferences", "SourceCandidate", "SourceConsent"]
