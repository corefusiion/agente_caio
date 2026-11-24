"""Known gaps: strict xfails document exploitability without changing other teams' code."""
import asyncio
import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from caiocore.agent.tools.filesystem import ReadFileTool
from caiocore.agent.tools.registry import ToolRegistry
from caiocore.server import api


def test_document_download_must_not_follow_external_symlink(tmp_path, monkeypatch):
    out = tmp_path / 'out'
    out.mkdir()
    secret = tmp_path / 'private-secret'
    secret.write_text('SYNTHETIC_PRIVATE_DATA')
    (out / 'report.txt').symlink_to(secret)
    monkeypatch.setattr(api, '_DOCS_OUT', str(out))
    monkeypatch.delenv('CAIO_API_TOKEN', raising=False)
    c = TestClient(api.app, base_url='http://localhost', client=('127.0.0.1', 12345))
    r = c.get('/api/documents/report.txt/download')
    assert 'SYNTHETIC_PRIVATE_DATA' not in r.text


def test_opt_in_workspace_read_must_not_leak_hardlinked_secret(tmp_path):
    work = tmp_path / 'work'
    work.mkdir()
    secret = tmp_path / 'private-secret'
    secret.write_text('SYNTHETIC_PRIVATE_DATA')
    os.link(secret, work / 'report.txt')
    reg = ToolRegistry(enable_workspace_file_tools=True)
    reg.register(ReadFileTool(workspace=work, allowed_dir=work))
    result = asyncio.run(reg.execute('read_file', {'path': 'report.txt'}))
    assert 'SYNTHETIC_PRIVATE_DATA' not in result


@pytest.mark.xfail(strict=True, reason='configured cross-origin browser preflight has no CORS support and cannot send bearer token')
def test_separate_dashboard_preflight_with_explicit_origin(tmp_path, monkeypatch):
    monkeypatch.setenv('CAIO_API_TOKEN', 'synthetic-token')
    monkeypatch.setenv('CAIO_ALLOWED_ORIGINS', 'http://localhost:8080')
    c = TestClient(api.app, base_url='http://localhost:18790', client=('127.0.0.1', 12345))
    r = c.options('/api/chat', headers={'origin':'http://localhost:8080', 'access-control-request-method':'POST', 'access-control-request-headers':'authorization,content-type'})
    assert r.status_code == 200
    assert r.headers.get('access-control-allow-origin') == 'http://localhost:8080'
