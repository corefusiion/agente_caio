"""Offline admission only: nothing here proves sandboxed video downloading."""
import builtins
import logging
import os
import socket
import subprocess
from unittest.mock import Mock, patch

import pytest

from caiocore.video_download import assess

YT = "https://www.youtube.com/watch?v=abcdefghijk"


@pytest.mark.parametrize("url,platform", [
    (YT, "youtube"),
    ("https://youtu.be/abcdefghijk", "youtube"),
    ("https://m.youtube.com/shorts/abcdefghijk", "youtube"),
    ("https://www.instagram.com/reel/Ab_c-1/", "instagram"),
    ("https://instagram.com/p/Ab_c-1", "instagram"),
    ("https://www.tiktok.com/@example.name/video/123456789/", "tiktok"),
])
def test_three_platforms_are_candidates_not_downloads(url, platform):
    port = Mock()
    port.authorize.return_value = True
    with patch.object(socket, "socket", side_effect=AssertionError("network")), \
         patch.object(socket, "getaddrinfo", side_effect=AssertionError("DNS")), \
         patch.object(subprocess, "Popen", side_effect=AssertionError("process")), \
         patch.object(os, "system", side_effect=AssertionError("shell")), \
         patch.object(builtins, "open", side_effect=AssertionError("file")):
        result = assess(url, consent=True, rights_confirmed=True, authorizer=port)
    assert result.status == "download_unavailable"
    assert result.platform == platform
    assert result.visibility_verified is False
    assert result.download_performed is False
    port.authorize.assert_called_once()
    assert "abcdefghijk" not in repr(port.authorize.call_args)


@pytest.mark.parametrize("url", [
    None, b"https://youtu.be/abcdefghijk", 3, "", "x" * 2049,
    "http://www.youtube.com/watch?v=abcdefghijk",
    "https://youtube.com.evil.test/watch?v=abcdefghijk",
    "https://youtube.com@evil.test/watch?v=abcdefghijk",
    "https://evil.test@youtube.com/watch?v=abcdefghijk",
    "https://youtube.com:443/watch?v=abcdefghijk",
    "https://youtube.com./watch?v=abcdefghijk",
    "https://127.0.0.1/watch?v=abcdefghijk",
    "https://[::1]/watch?v=abcdefghijk",
    "https://169.254.169.254/latest/meta-data/",
    "https://192.168.1.1/", "file:///etc/passwd",
    "https://youtube.com/watch?v=abcdefghijk&redirect=https://127.0.0.1",
    "https://youtube.com/watch?v=abcdefghijk&v=abcdefghijk",
    "https://youtube.com/watch?v=abcdefghijk&token=SECRET",
    "https://youtube.com/watch?v=abcdefghijk#fragment",
    "https://youtube.com/watch?v=abcdefghi%6ak",
    "https://youtube.com/watch?v=abcdefghijk\n",
    "https://youtube.com/watch?v=abc\tdefghijk",
    "https://youtube.com\\@evil.test/watch?v=abcdefghijk",
    "https://youtubе.com/watch?v=abcdefghijk",  # Cyrillic confusable
    "https://www.instagram.com/accounts/login/",
    "https://www.instagram.com/reel/../private/",
    "https://vm.tiktok.com/abcd/",  # needs redirect; unsupported
    "https://www.tiktok.com/@x/video/123?token=SECRET",
    "https://www.tiktok.com/@x/video/123/extra",
])
def test_rejects_untrusted_url_before_authorizer(url):
    port = Mock()
    assert assess(url, consent=True, rights_confirmed=True, authorizer=port).status == "invalid_url"
    port.authorize.assert_not_called()


@pytest.mark.parametrize("consent,rights", [(False, True), (True, False), (1, True),
                                            (True, 1), (None, True), ("yes", True)])
def test_explicit_consent_and_rights_required(consent, rights):
    port = Mock()
    result = assess(YT, consent=consent, rights_confirmed=rights, authorizer=port)
    assert result.status == "authorization_required"
    port.authorize.assert_not_called()


@pytest.mark.parametrize("answer", [False, None, 1, "yes"])
def test_port_must_explicitly_authorize(answer):
    port = Mock()
    port.authorize.return_value = answer
    assert assess(YT, consent=True, rights_confirmed=True, authorizer=port).status == "authorization_denied"


def test_default_is_closed_and_port_failure_is_sanitized(caplog):
    assert assess(YT, consent=True, rights_confirmed=True).status == "authorization_required"
    port = Mock()
    port.authorize.side_effect = RuntimeError("SECRET " + YT)
    with caplog.at_level(logging.DEBUG):
        result = assess(YT, consent=True, rights_confirmed=True, authorizer=port)
    assert result.status == "authorization_denied"
    assert "SECRET" not in repr(result) + caplog.text
    assert YT not in repr(result) + caplog.text


def test_private_or_paid_links_not_claimed_public_or_downloaded():
    # URL syntax cannot distinguish public, private, members-only or paid content.
    port = Mock()
    port.authorize.return_value = True
    result = assess(YT, consent=True, rights_confirmed=True, authorizer=port)
    assert result.status == "download_unavailable"
    assert not result.visibility_verified and not result.download_performed
