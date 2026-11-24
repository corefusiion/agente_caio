"""Response-level fail-closed and descriptor-lifetime tests."""
import asyncio
import os
import pytest
from fastapi.testclient import TestClient
from caiocore.server import api

pytestmark = pytest.mark.skipif(not api._document_secure_open_supported(), reason='Linux descriptor-based open required')


def test_no_descriptor_path_fails_closed(tmp_path, monkeypatch):
    out = tmp_path / 'out'
    out.mkdir()
    (out / 'report.txt').write_text('private')
    monkeypatch.setattr(api, '_DOCS_OUT', str(out))
    monkeypatch.delenv('CAIO_API_TOKEN', raising=False)
    real_stat = os.stat
    def unavailable(path, *args, **kwargs):
        if str(path).startswith('/proc/self/fd/'):
            raise FileNotFoundError('proc unavailable')
        return real_stat(path, *args, **kwargs)
    monkeypatch.setattr(os, 'stat', unavailable)
    c = TestClient(api.app, base_url='http://localhost', client=('127.0.0.1', 12345))
    r = c.get('/api/documents/report.txt/download')
    assert r.status_code == 503 and b'private' not in r.content


def test_pathsend_never_reopens_after_descriptor_closed(tmp_path):
    target = tmp_path / 'report.txt'
    target.write_text('safe')
    fd, info = api._open_document(str(tmp_path), target.name)
    response = api._DocumentDownloadResponse(fd, info, target.name)
    messages = []
    async def send(msg):
        messages.append(msg)
    async def receive():
        return {'type': 'http.request'}
    scope = {'type': 'http', 'asgi': {'spec_version': '2.4'}, 'method': 'GET',
             'headers': [], 'extensions': {'http.response.pathsend': {}}}
    asyncio.run(response(scope, receive, send))
    assert all(m['type'] != 'http.response.pathsend' for m in messages)
    assert b''.join(m.get('body', b'') for m in messages) == b'safe'
    with pytest.raises(OSError):
        os.fstat(fd)
