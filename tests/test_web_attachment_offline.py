"""Offline-only tests; no endpoint, external provider, network or real upload."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from caiocore.server.attachment_offline import (
    AttachmentError, MAX_CHUNK_BYTES, MAX_FILE_BYTES, MAX_FILE_CHARS,
    MAX_TOTAL_BYTES, OfflineText, TextCollector, build_text_request,
)


def collect(data, mime="text/plain"):
    c = TextCollector(mime, len(data))
    for i in range(0, len(data), MAX_CHUNK_BYTES):
        c.feed(data[i:i + MAX_CHUNK_BYTES])
    return c.finish()


def test_incremental_utf8_and_csv_not_interpreted():
    c = TextCollector("text/csv", 4)
    c.feed(b"\xc3")
    c.feed(b"\xa9,1")
    result = c.finish()
    assert result.text == "é,1"
    assert result.byte_count == 4
    assert "é" not in repr(result)
    req = build_text_request("Resuma", [result])
    assert req[0]["role"] == "system"
    assert req[-1]["content"].endswith("é,1")


@pytest.mark.parametrize("mime", ["image/jpeg", "image/png", "image/webp", "application/pdf", "application/zip", "text/html", "text/plain; charset=utf-8", "text/plain\n"])
def test_unsupported_including_images_pdf_and_archives(mime):
    with pytest.raises(AttachmentError, match="unsupported_type"):
        TextCollector(mime, 1)


@pytest.mark.parametrize("data", [b"\xff", b"\xc3", b"a\x00b", b"a\x1bb", b"\x7f"])
def test_reject_invalid_text(data):
    with pytest.raises(AttachmentError, match="invalid_text"):
        collect(data)


def test_sizes_chunks_and_failure_closed():
    for size in (0, -1, True):
        with pytest.raises(AttachmentError, match="invalid_size"):
            TextCollector("text/plain", size)
    with pytest.raises(AttachmentError, match="file_too_large"):
        TextCollector("text/plain", MAX_FILE_BYTES + 1)
    c = TextCollector("text/plain", 5)
    c.feed(b"ab")
    with pytest.raises(AttachmentError, match="size_mismatch"):
        c.finish()
    with pytest.raises(AttachmentError, match="collector_closed"):
        c.feed(b"x")
    c = TextCollector("text/plain", 1)
    with pytest.raises(AttachmentError, match="size_mismatch"):
        c.feed(b"ab")
    c = TextCollector("text/plain", MAX_CHUNK_BYTES + 1)
    with pytest.raises(AttachmentError, match="invalid_chunk"):
        c.feed(b"x" * (MAX_CHUNK_BYTES + 1))
    with pytest.raises(AttachmentError, match="text_too_long"):
        collect(b"x" * (MAX_FILE_CHARS + 1))


def test_request_limits_and_forgery():
    with pytest.raises(AttachmentError, match="invalid_file_count"):
        build_text_request("x", [])
    with pytest.raises(AttachmentError, match="invalid_question"):
        build_text_request(" ", [collect(b"x")])
    with pytest.raises(AttachmentError, match="invalid_file"):
        build_text_request("x", [OfflineText("text/plain", "a", 2)])
    with pytest.raises(AttachmentError, match="invalid_file"):
        build_text_request("x", [OfflineText([], "a", 1)])
    # Per-file char cap still allows >512 KiB in three UTF-8 files.
    f = collect(("é" * 100_000).encode())
    assert f.byte_count * 3 > MAX_TOTAL_BYTES
    with pytest.raises(AttachmentError, match="total_too_large"):
        build_text_request("x", [f, f, f])


def test_codex_image_conversion_synthetic_only():
    from caiocore.providers.openai_codex_provider import _convert_user_message
    url = "data:image/png;base64,aGVsbG8="  # synthetic bytes, not a real image
    r = _convert_user_message([{"type": "text", "text": "analise"},
                               {"type": "image_url", "image_url": {"url": url}}])
    assert r["content"][1] == {"type": "input_image", "image_url": url, "detail": "auto"}
    # This verifies the adapter shape only, not model/provider vision capability.


def test_custom_provider_passes_synthetic_multimodal_to_mock_only():
    from caiocore.providers.custom_provider import CustomProvider
    p = CustomProvider(api_key="fake", api_base="http://127.0.0.1:1/v1", default_model="mock")
    create = AsyncMock(return_value=SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content="mock", tool_calls=[], reasoning_content=None), finish_reason="stop")], usage=None))
    p._client.chat.completions.create = create
    content = [{"type": "text", "text": "teste"}, {"type": "image_url", "image_url": {"url": "data:image/png;base64,aGVsbG8="}}]
    result = asyncio.run(p.chat([{"role": "user", "content": content}], tools=None))
    assert result.content == "mock"
    sent = create.await_args.kwargs
    assert sent["messages"][0]["content"] == content
    assert "tools" not in sent


def test_custom_provider_error_is_not_success():
    from caiocore.providers.custom_provider import CustomProvider
    p = CustomProvider(api_key="fake", api_base="http://127.0.0.1:1/v1", default_model="mock")
    p._client.chat.completions.create = AsyncMock(side_effect=RuntimeError("test failure"))
    r = asyncio.run(p.chat([{"role": "user", "content": "x"}]))
    assert r.finish_reason == "error"
