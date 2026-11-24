"""Private, offline preference storage; not an authentication boundary."""
from .store import ConsentRequired, ProfileStore

__all__ = ["ConsentRequired", "ProfileStore"]
