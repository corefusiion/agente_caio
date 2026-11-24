"""Offline regression tests: OAuth errors must not echo credentials."""
import json
import sys
import types
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from loguru import logger
from caiocore.server import api

SECRET = 'synthetic-secret-never-log'


@pytest.fixture
def oauth_environment(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv('CAIO_API_TOKEN', raising=False)
    monkeypatch.setattr(api, '_config', None)
    messages = []
    sink = logger.add(messages.append, format='{message}')
    class BrokenFlow:
        @classmethod
        def from_client_secrets_file(cls, *args, **kwargs):
            raise RuntimeError('remote said ' + SECRET)
    fake_flow = types.ModuleType('google_auth_oauthlib.flow')
    fake_flow.Flow = BrokenFlow
    fake_package = types.ModuleType('google_auth_oauthlib')
    fake_package.__path__ = []
    monkeypatch.setitem(sys.modules, 'google_auth_oauthlib', fake_package)
    monkeypatch.setitem(sys.modules, 'google_auth_oauthlib.flow', fake_flow)
    yield tmp_path, messages
    logger.remove(sink)


def test_setup_oauth_error_does_not_echo_secrets(oauth_environment):
    _, messages = oauth_environment
    c = TestClient(api.app, base_url='http://localhost', client=('127.0.0.1', 12345))
    r = c.post('/api/settings/credentials/google', headers={'origin':'http://localhost'}, json={'content': json.dumps({'installed': {'client_id':'synthetic'}})})
    assert r.status_code == 500
    assert SECRET not in r.text
    assert SECRET not in ''.join(messages)


def test_confirm_oauth_error_does_not_echo_secrets(oauth_environment):
    tmp_path, messages = oauth_environment
    folder = tmp_path / 'caio-stack' / 'core'
    folder.mkdir(parents=True)
    (folder / 'credentials.json').write_text(json.dumps({'installed': {'client_id':'synthetic'}}))
    c = TestClient(api.app, base_url='http://localhost', client=('127.0.0.1', 12345))
    r = c.post('/api/settings/credentials/google/confirm', headers={'origin':'http://localhost'}, json={'response_url':'http://localhost/?code=' + SECRET})
    assert r.status_code == 500
    assert SECRET not in r.text
    assert SECRET not in ''.join(messages)


def test_token_save_error_does_not_echo_secrets(oauth_environment, monkeypatch):
    _, messages = oauth_environment
    fake_loader = types.ModuleType('caiocore.config.loader')
    fake_loader.get_data_dir = lambda: None
    def broken_save(config):
        raise RuntimeError('remote said ' + SECRET)
    fake_loader.save_config = broken_save
    monkeypatch.setitem(sys.modules, 'caiocore.config.loader', fake_loader)
    monkeypatch.setattr(api, '_config', SimpleNamespace(tools=SimpleNamespace(google_calendar=SimpleNamespace())))
    c = TestClient(api.app, base_url='http://localhost', client=('127.0.0.1', 12345))
    r = c.post('/api/settings/credentials/google/token_direct', headers={'origin':'http://localhost'}, json={'token_content': json.dumps({'token':'synthetic'})})
    assert r.status_code == 400
    assert SECRET not in r.text
    assert SECRET not in ''.join(messages)


def test_setup_invalid_content_preserves_400(oauth_environment):
    c = TestClient(api.app, base_url='http://localhost', client=('127.0.0.1', 12345))
    r = c.post('/api/settings/credentials/google', headers={'origin':'http://localhost'}, json={'content':'invalid'})
    assert r.status_code == 400
