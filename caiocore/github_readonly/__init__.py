"""Isolated GitHub REST reader; no integration or network enabled by default."""
from .client import GitHubReadOnly, GitHubReadOnlyError, HTTPSReadTransport

__all__ = ["GitHubReadOnly", "GitHubReadOnlyError", "HTTPSReadTransport"]
