"""Fila local: o núcleo não autentica; o worker restrito é opt-in separado."""
from .store import Queue, QueueError, InvalidTransition, StorageSecurityError

__all__ = ["Queue", "QueueError", "InvalidTransition", "StorageSecurityError"]
