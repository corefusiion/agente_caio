"""Adversarial rename at the precise file-open boundary."""
import os
import pytest
from fastapi.testclient import TestClient
from caiocore.server import api


@pytest.mark.skipif(not api._document_secure_open_supported(), reason='POSIX descriptor-based open required')
def test_replace_with_symlink_just_before_open(tmp_path, monkeypatch):
    out = tmp_path / 'out'
    out.mkdir()
    target = out / 'report.txt'
    target.write_text('safe')
    secret = tmp_path / 'secret'
    secret.write_text('SYNTHETIC_PRIVATE_DATA')
    monkeypatch.setattr(api, '_DOCS_OUT', str(out))
    monkeypatch.delenv('CAIO_API_TOKEN', raising=False)
    real_open = os.open
    swapped = False
    def racing_open(path, flags, *args, **kwargs):
        nonlocal swapped
        if path == 'report.txt' and kwargs.get('dir_fd') is not None:
            target.unlink()
            target.symlink_to(secret)
            swapped = True
        return real_open(path, flags, *args, **kwargs)
    monkeypatch.setattr(api, '_document_secure_open_supported', lambda: True)
    monkeypatch.setattr(os, 'open', racing_open)
    c = TestClient(api.app, base_url='http://localhost', client=('127.0.0.1', 12345))
    r = c.get('/api/documents/report.txt/download')
    assert swapped and r.status_code == 404
    assert b'SYNTHETIC_PRIVATE_DATA' not in r.content
