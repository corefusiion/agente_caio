"""Offline-only experiment for bounded, local Telegram document inspection.

Not connected to TelegramChannel. The injected source is a test seam, NOT a
validated PTB transport. Never use it with a real bot without a security review.
"""
from __future__ import annotations

import asyncio
import codecs
import inspect
import re
from contextlib import aclosing
import sys
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from typing import Any

MAX_BYTES = 1024 * 1024
MAX_CHUNK_BYTES = 64 * 1024
TIMEOUT_SECONDS = 10.0


class AttachmentRejected(Exception):
    """Generic rejection; callers must not include original exception or data."""


@dataclass(frozen=True)
class OfflineAttachmentConfig:
    enabled: bool = False
    allowed_user_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class Inspection:
    """Local statistics, not an interpretation or proof of content safety."""
    bytes_received: int
    characters: int
    newline_count: int


def _id(value: Any) -> str | None:
    if type(value) is int:
        value = str(value)
    if type(value) is not str or not re.fullmatch(r'[1-9][0-9]{0,18}', value):
        return None
    return value


def _authorize(update: Any, config: OfflineAttachmentConfig) -> bool:
    """Do not inspect media or instantiate a source until identity is checked."""
    if type(config) is not OfflineAttachmentConfig or config.enabled is not True:
        return False
    if type(config.allowed_user_ids) is not tuple:
        return False
    msg = getattr(update, 'message', None)
    user = getattr(update, 'effective_user', None)
    if msg is None or user is None:
        return False
    uid = _id(getattr(user, 'id', None))
    chat = getattr(msg, 'chat', None)
    return bool(uid and uid in config.allowed_user_ids
                and _id(getattr(msg, 'chat_id', None)) == uid
                and _id(getattr(chat, 'id', None)) == uid
                and _id(getattr(getattr(msg, 'from_user', None), 'id', None)) == uid
                and getattr(chat, 'type', None) == 'private'
                and getattr(user, 'is_bot', None) is False
                and getattr(msg, 'sender_chat', None) is None
                and not getattr(msg, 'business_connection_id', None)
                and not getattr(msg, 'forward_origin', None)
                and not getattr(msg, 'forward_from', None)
                and not getattr(msg, 'forward_from_chat', None)
                and not getattr(msg, 'is_automatic_forward', False))


async def inspect_document_offline(
    update: Any,
    config: OfflineAttachmentConfig,
    source_factory: Callable[[str], AsyncIterator[bytes]],
) -> Inspection:
    """Inspect one synthetic text document, locally and without file storage.

    source_factory is invoked only after authorization, opt-in and metadata
    checks. It must yield bounded chunks and cooperate with cancellation.
    This function has no implementation of a network source and is not wired
    to the message bus. No result contains text, a file path or a file ID.
    """
    try:
        if sys.platform != 'linux' or not _authorize(update, config):
            raise AttachmentRejected()
        msg = update.message
        document = getattr(msg, 'document', None)
        if document is None or getattr(document, 'mime_type', None) != 'text/plain':
            raise AttachmentRejected()
        size = getattr(document, 'file_size', None)
        file_id = getattr(document, 'file_id', None)
        if (type(size) is not int or not 0 < size <= MAX_BYTES
                or type(file_id) is not str or not 1 <= len(file_id) <= 512
                or not file_id.isascii() or not re.fullmatch(r'[A-Za-z0-9_-]+', file_id)):
            raise AttachmentRejected()
        count = chars = lines = 0
        decoder = codecs.getincrementaldecoder('utf-8')('strict')
        # The transport must not pre-buffer the response. PTB's stock download
        # helpers have not been shown to provide this guarantee.
        async with asyncio.timeout(TIMEOUT_SECONDS):
            source = source_factory(file_id)
            if not inspect.isasyncgen(source):
                raise AttachmentRejected()
            async with aclosing(source):
                async for chunk in source:
                    if type(chunk) is not bytes or not 0 < len(chunk) <= MAX_CHUNK_BYTES:
                        raise AttachmentRejected()
                    count += len(chunk)
                    if count > size or count > MAX_BYTES:
                        raise AttachmentRejected()
                    text = decoder.decode(chunk, final=False)
                    if any(ord(c) < 32 and c not in '\t\n\r' or ord(c) == 127 for c in text):
                        raise AttachmentRejected()
                    chars += len(text)
                    lines += text.count('\n')
            text = decoder.decode(b'', final=True)
            chars += len(text)
            lines += text.count('\n')
        if count != size:
            raise AttachmentRejected()
        return Inspection(count, chars, lines)
    except asyncio.CancelledError:
        raise
    except Exception:
        # Avoid propagating transport exceptions, file IDs, message contents or
        # tokens in the exception string. Caller must also avoid traceback logs.
        raise AttachmentRejected('Anexo não recebido nem analisado.') from None
