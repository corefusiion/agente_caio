import asyncio
import os
import sqlite3
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from caiocore.server import api
from caiocore.task_queue import Queue

TOKEN = 'local-test-token-' + 'x' * 40
PATH = '/api/local-queue/tasks'
HEADERS = {'authorization': 'Bearer ' + TOKEN}

@pytest.fixture
def setup(monkeypatch, tmp_path):
    for key in ('CAIO_API_TOKEN', 'CAIO_LOCAL_QUEUE_ENABLED', 'CAIO_LOCAL_QUEUE_DIR', 'CAIO_ALLOWED_HOSTS', 'CAIO_ALLOWED_ORIGINS'):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(api, '_local_queue_server_loopback', False)
    monkeypatch.setattr(api, '_agent', None)
    directory = tmp_path / 'q'
    directory.mkdir(mode=0o700)
    c = TestClient(api.app, base_url='http://127.0.0.1', client=('127.0.0.1', 1234))
    return c, directory


def enable(monkeypatch, directory):
    monkeypatch.setenv('CAIO_API_TOKEN', TOKEN)
    monkeypatch.setenv('CAIO_LOCAL_QUEUE_ENABLED', '1')
    monkeypatch.setenv('CAIO_LOCAL_QUEUE_DIR', str(directory))
    monkeypatch.setattr(api, '_local_queue_server_loopback', True)


def test_legacy_disabled_no_agent_or_mutation(setup, monkeypatch):
    c, directory = setup
    class Trap:
        async def process_direct(self, *args, **kwargs):
            raise AssertionError('must never run')
    monkeypatch.setattr(api, '_agent', Trap())
    for method, url in [('get', '/api/tasks'), ('post', '/api/tasks'), ('post', '/api/tasks/id/execute'), ('delete', '/api/tasks/id')]:
        r = getattr(c, method)(url, headers={'origin': 'http://127.0.0.1'})
        assert r.status_code == 503
    assert not list(directory.iterdir())
    monkeypatch.setattr(api, '_agent', None)
    assert c.post('/api/tasks/id/execute', headers={'origin': 'http://127.0.0.1'}).status_code == 503


def test_opt_in_token_and_loopback_mandatory(setup, monkeypatch):
    c, directory = setup
    assert c.get(PATH).status_code == 503
    enable(monkeypatch, directory)
    assert c.get(PATH).status_code == 401
    assert c.get(PATH, headers={'authorization': 'Bearer wrong'}).status_code == 401
    assert c.get(PATH, headers=HEADERS).json() == []
    monkeypatch.setattr(api, '_local_queue_server_loopback', False)
    assert c.get(PATH, headers=HEADERS).status_code == 503
    monkeypatch.setattr(api, '_local_queue_server_loopback', True)
    monkeypatch.setenv('CAIO_API_TOKEN', 'weak')
    assert c.get(PATH, headers={'authorization': 'Bearer weak'}).status_code == 503
    assert not (directory / 'tasks.sqlite3').exists() or Queue(directory, 'local_operator').list() == []


def test_remote_proxy_origin_and_host_denied(setup, monkeypatch):
    c, directory = setup
    enable(monkeypatch, directory)
    monkeypatch.setenv('CAIO_ALLOWED_HOSTS', 'example.com')
    monkeypatch.setenv('CAIO_ALLOWED_ORIGINS', 'http://evil.example')
    remote = TestClient(api.app, base_url='http://127.0.0.1', client=('192.0.2.3', 9))
    assert remote.get(PATH, headers=HEADERS).status_code == 403
    for h in ({'x-forwarded-for':'127.0.0.1'}, {'forwarded':'for=127.0.0.1'}, {'x-real-ip':'127.0.0.1'}, {'origin':'http://evil.example'}, {'host':'example.com'}):
        assert c.get(PATH, headers={**HEADERS, **h}).status_code == 403
    remote_server = TestClient(api.app, base_url='http://example.com', client=('127.0.0.1', 9))
    assert remote_server.get(PATH, headers=HEADERS).status_code == 403
    assert not list(directory.iterdir())


def test_crud_is_single_owner_audited_and_never_executes(setup, monkeypatch):
    c, directory = setup
    enable(monkeypatch, directory)
    other = Queue(directory, 'other_user').create('private other user')
    assert c.post(PATH, headers=HEADERS, json={'description':'x', 'owner':'other_user'}).status_code == 422
    assert c.get(PATH+'?owner=other_user', headers=HEADERS).status_code == 422
    created = c.post(PATH, headers=HEADERS, json={'description':'prepare', 'priority':3})
    assert created.status_code == 201
    task = created.json()
    assert task['status'] == 'pendente' and 'owner' not in task and 'claim_token' not in task
    taskid = task['id']
    assert c.post(PATH+'/'+taskid+'/cancel', headers=HEADERS, json={'owner':'other_user'}).status_code == 422
    assert c.get(PATH, headers=HEADERS).json() == [task]
    assert c.get(PATH+'/'+other['id'], headers=HEADERS).status_code == 404
    assert c.get(PATH+'/'+taskid, headers=HEADERS).json() == task
    assert c.get(PATH, headers=HEADERS).headers['cache-control'] == 'no-store'
    assert c.post(PATH+'/'+taskid+'/execute', headers=HEADERS).status_code in (404, 405)
    assert c.post(PATH+'/'+taskid+'/complete', headers=HEADERS, json={'evidence':'fake'}).status_code in (404, 405)
    assert c.post(PATH+'/'+taskid+'/cancel', headers=HEADERS).json()['status'] == 'cancelada'
    assert c.post(PATH+'/'+taskid+'/cancel', headers=HEADERS).status_code == 409
    assert c.post(PATH+'/'+'f'*32+'/cancel', headers=HEADERS).status_code == 409
    audit = c.get('/api/local-queue/audit', headers=HEADERS).json()
    assert [event['action'] for event in audit] == ['criada', 'cancelada']
    assert Queue(directory, 'other_user').get(other['id'])['description'] == 'private other user'
    assert 'private other user' not in str(audit)


def test_validation_storage_fail_closed(setup, monkeypatch):
    c, directory = setup
    enable(monkeypatch, directory)
    for body in ({'description':''}, {'description':'x','priority':True}, {'description':'x','priority':'1'}, {'description':'x','priority':11}, {'description':'x','other':'ignored'}):
        assert c.post(PATH, headers=HEADERS, json=body).status_code == 422
    assert c.get(PATH+'?limit=201', headers=HEADERS).status_code == 422
    assert c.get(PATH+'/not-an-id', headers=HEADERS).status_code == 422
    assert c.get(PATH, headers=HEADERS).json() == []
    directory.chmod(0o755)
    r = c.post(PATH, headers=HEADERS, json={'description':'must fail'})
    assert r.status_code == 503 and str(directory) not in r.text
    directory.chmod(0o700)
    assert c.get(PATH, headers=HEADERS).json() == []
    monkeypatch.setenv('CAIO_LOCAL_QUEUE_DIR', 'relative')
    assert c.get(PATH, headers=HEADERS).status_code == 503


def test_injected_database_error_is_not_success_or_execution(setup, monkeypatch):
    c, directory = setup
    enable(monkeypatch, directory)
    class AgentTrap:
        async def process_direct(self, *args, **kwargs):
            raise AssertionError('agent must not be called')
    monkeypatch.setattr(api, '_agent', AgentTrap())
    original = Queue.create
    def fail(*args, **kwargs):
        raise sqlite3.OperationalError('secret internal database path')
    monkeypatch.setattr(Queue, 'create', fail)
    result = c.post(PATH, headers=HEADERS, json={'description': 'must not claim success'})
    assert result.status_code == 503 and 'secret internal' not in result.text
    monkeypatch.setattr(Queue, 'create', original)
    assert c.get(PATH, headers=HEADERS).json() == []
    created = c.post(PATH, headers=HEADERS, json={'description': 'only recorded'}).json()
    assert created['status'] == 'pendente'
    assert c.get(PATH, headers=HEADERS).json()[0]['status'] == 'pendente'


def test_server_flag_only_for_loopback_and_reset(monkeypatch):
    observed = []
    class FakeServer:
        def __init__(self, config): pass
        async def serve(self):
            observed.append(api._local_queue_server_loopback)
    monkeypatch.setattr('uvicorn.Server', FakeServer)
    asyncio.run(api.start_api_server(host='0.0.0.0'))
    asyncio.run(api.start_api_server(host='127.0.0.1'))
    assert observed == [False, True]
    assert api._local_queue_server_loopback is False
