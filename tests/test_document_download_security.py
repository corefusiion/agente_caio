"""Security regression tests for document downloads; no external services."""
import asyncio
import os

import pytest
from fastapi.testclient import TestClient
from caiocore.server import api

pytestmark = pytest.mark.skipif(not api._document_secure_open_supported(), reason='POSIX descriptor-based open required')


@pytest.fixture
def setup_download(tmp_path, monkeypatch):
    out = tmp_path / 'out'
    out.mkdir()
    home = tmp_path / 'home'
    home.mkdir()
    monkeypatch.setattr(api, '_DOCS_OUT', str(out))
    monkeypatch.setenv('HOME', str(home))
    monkeypatch.delenv('CAIO_API_TOKEN', raising=False)
    monkeypatch.delenv('CAIO_ALLOWED_ORIGINS', raising=False)
    client = TestClient(api.app, base_url='http://localhost', client=('127.0.0.1', 12345))
    return out, home, client


def test_regular_document_and_agent_fallback(setup_download):
    out, home, client = setup_download
    (out / 'report.txt').write_text('local content')
    agent = home / '.caiocore' / 'workspace' / 'out'
    agent.mkdir(parents=True)
    (agent / 'report.txt').write_text('agent content')
    (agent / 'other.txt').write_text('fallback content')
    r = client.get('/api/documents/report.txt/download')
    assert r.status_code == 200 and r.content == b'local content'
    assert r.headers['content-disposition'] == 'attachment; filename="report.txt"'
    assert r.headers['content-type'].startswith('text/plain')
    r = client.get('/api/documents/report.txt/download', headers={'range': 'bytes=0-4'})
    assert r.status_code == 206 and r.content == b'local'
    assert r.headers['content-range'] == 'bytes 0-4/13'
    r = client.get('/api/documents/other.txt/download')
    assert r.status_code == 200 and r.content == b'fallback content'
    assert client.get('/api/documents/missing.txt/download').status_code == 404


@pytest.mark.parametrize('kind', ['external_symlink', 'internal_symlink', 'broken_symlink', 'hardlink', 'directory', 'fifo'])
def test_reject_unsafe_entries_without_fallback(setup_download, tmp_path, kind):
    out, home, client = setup_download
    secret = tmp_path / 'secret'
    secret.write_text('SYNTHETIC_PRIVATE_DATA')
    agent = home / '.caiocore' / 'workspace' / 'out'
    agent.mkdir(parents=True)
    (agent / 'report.txt').write_text('should not fall back')
    target = out / 'report.txt'
    if kind == 'external_symlink':
        target.symlink_to(secret)
    elif kind == 'internal_symlink':
        (out / 'real.txt').write_text('internal')
        target.symlink_to(out / 'real.txt')
    elif kind == 'broken_symlink':
        target.symlink_to(tmp_path / 'missing')
    elif kind == 'hardlink':
        os.link(secret, target)
    elif kind == 'directory':
        target.mkdir()
    else:
        os.mkfifo(target)
    r = client.get('/api/documents/report.txt/download')
    assert r.status_code == 404
    assert b'SYNTHETIC_PRIVATE_DATA' not in r.content
    assert b'should not fall back' not in r.content


@pytest.mark.parametrize('which', ['out', 'ancestor'])
def test_reject_symlinked_directory_components(setup_download, tmp_path, monkeypatch, which):
    out, _, client = setup_download
    outside = tmp_path / 'outside'
    outside.mkdir()
    if which == 'out':
        (outside / 'report.txt').write_text('SYNTHETIC_PRIVATE_DATA')
        out.rmdir()
        out.symlink_to(outside, target_is_directory=True)
    else:
        (outside / 'out').mkdir()
        (outside / 'out' / 'report.txt').write_text('SYNTHETIC_PRIVATE_DATA')
        link = tmp_path / 'link'
        link.symlink_to(outside, target_is_directory=True)
        monkeypatch.setattr(api, '_DOCS_OUT', str(link / 'out'))
    r = client.get('/api/documents/report.txt/download')
    assert r.status_code == 404 and b'SYNTHETIC_PRIVATE_DATA' not in r.content


def test_path_swap_after_open_cannot_redirect_stream(setup_download, tmp_path, monkeypatch):
    out, _, client = setup_download
    target = out / 'report.txt'
    target.write_text('safe document')
    secret = tmp_path / 'secret'
    secret.write_text('SYNTHETIC_PRIVATE_DATA')
    original_open = api._open_document
    def swap_after_open(directory, filename):
        fd, info = original_open(directory, filename)
        target.unlink()
        target.symlink_to(secret)
        return fd, info
    monkeypatch.setattr(api, '_open_document', swap_after_open)
    r = client.get('/api/documents/report.txt/download')
    assert r.status_code == 200 and r.content == b'safe document'


def test_unavailable_secure_open_fails_closed(setup_download, monkeypatch):
    out, _, client = setup_download
    (out / 'report.txt').write_text('private')
    monkeypatch.setattr(api, '_document_secure_open_supported', lambda: False)
    r = client.get('/api/documents/report.txt/download')
    assert r.status_code == 503 and b'private' not in r.content


@pytest.mark.parametrize('name', ['', '.', '..', '../secret', 'nested/file', 'nested\\file', 'a\x00b'])
def test_reject_non_basename_directly(setup_download, name):
    out, _, _ = setup_download
    with pytest.raises(OSError):
        api._open_document(str(out), name)


def test_descriptor_closed_if_response_send_fails(setup_download):
    out, _, _ = setup_download
    (out / 'report.txt').write_text('safe')
    fd, info = api._open_document(str(out), 'report.txt')
    response = api._DocumentDownloadResponse(fd, info, 'report.txt')
    async def fail_send(message):
        raise RuntimeError('simulated disconnect')
    async def receive():
        return {'type': 'http.request'}
    scope = {'type': 'http', 'asgi': {'spec_version': '2.4'}, 'method': 'GET', 'headers': []}
    with pytest.raises(RuntimeError, match='simulated disconnect'):
        asyncio.run(response(scope, receive, fail_send))
    with pytest.raises(OSError):
        os.fstat(fd)
