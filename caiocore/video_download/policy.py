"""Narrow offline URL admission. This module cannot download or check visibility.

The injected authorization port is trusted application code, not a sandbox.
No execution port is provided until an independently enforced sandbox exists.
"""
from dataclasses import dataclass, field
import re
from typing import Protocol

# Deliberately fewer forms than the sites support: no query secrets, redirects,
# shorteners (except direct youtu.be IDs), encoded characters, or extra paths.
_PATTERNS = (
    ("youtube", re.compile(r"https://(?:www\.|m\.)?youtube\.com/watch\?v=([A-Za-z0-9_-]{11})")),
    ("youtube", re.compile(r"https://(?:www\.|m\.)?youtube\.com/shorts/([A-Za-z0-9_-]{11})")),
    ("youtube", re.compile(r"https://youtu\.be/([A-Za-z0-9_-]{11})")),
    ("instagram", re.compile(r"https://(?:www\.)?instagram\.com/(?:reel|p)/([A-Za-z0-9_-]{1,32})/?")),
    ("tiktok", re.compile(r"https://(?:www\.)?tiktok\.com/@[A-Za-z0-9._]{1,24}/video/([0-9]{1,25})/?")),
)


@dataclass(frozen=True)
class Candidate:
    platform: str
    # The identifier can itself be sensitive. Never put it in repr/logs/results.
    identifier: str = field(repr=False)


class AuthorizationPort(Protocol):
    def authorize(self, candidate: Candidate) -> bool:
        """Decide offline for this principal and candidate. Exactly True to permit."""
        ...


@dataclass(frozen=True)
class Assessment:
    status: str
    platform: str | None = None
    visibility_verified: bool = False
    download_performed: bool = False


def _candidate(url: object) -> Candidate | None:
    if not isinstance(url, str) or len(url) > 2048 or not url.isascii():
        return None
    for platform, pattern in _PATTERNS:
        match = pattern.fullmatch(url)
        if match:
            return Candidate(platform, match.group(1))
    return None


def assess(url: object, *, consent: object = False, rights_confirmed: object = False,
           authorizer: AuthorizationPort | None = None) -> Assessment:
    """Assess syntax and offline permission only. Never downloads or checks public status.

    Consent and rights confirmation are separate, explicit declarations; neither
    establishes public visibility or bypasses any platform restriction.
    """
    candidate = _candidate(url)
    if candidate is None:
        return Assessment("invalid_url")
    if consent is not True or rights_confirmed is not True:
        return Assessment("authorization_required", candidate.platform)
    if authorizer is None:
        return Assessment("authorization_required", candidate.platform)
    try:
        authorized = authorizer.authorize(candidate) is True
    except Exception:
        # The exception may contain a secret or URL: never propagate it.
        authorized = False
    if not authorized:
        return Assessment("authorization_denied", candidate.platform)
    # Even authorized syntactic candidates can point to private or paid content.
    return Assessment("download_unavailable", candidate.platform)
