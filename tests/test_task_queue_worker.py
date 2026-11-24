import json
import os
import sqlite3
import subprocess
import sys
import time

import pytest
from fastapi.testclient import TestClient

from caiocore.task_queue import Queue, InvalidTransition
from caiocore.task_queue.worker import LocalTextWorker
from caiocore.server import api


@pytest.fixture
def directory(tmp_path):
    path = tmp_path / 'queue'
    path.mkdir(mode=0o700)
    return path


def wait_for(test, timeout=6):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = test()
        if value:
            return value
        time.sleep(.02)
    raise AssertionError('timed out')


def test_real_processing_durable_and_legacy_not_executed(directory):
    q = Queue(directory, 'local_operator')
    legacy = q.create('legacy never authorized', 10)
    with pytest.raises(ValueError):
        q.create_text_stats('missing consent')
    with pytest.raises(ValueError):
        q.create_text_stats('not a bool', consent=1)
    task = q.create_text_stats('Olá mundo\nrm -rf /', consent=True)
    # Legacy claim cannot consume the executable lane.
    assert q.claim()[0]['id'] == legacy['id']
    assert q.claim() is None
    worker = LocalTextWorker(q, interval=.01)
    worker.start()
    try:
        result = wait_for(lambda: (x if (x := q.get(task['id']))['status'] == 'concluida' else None))
        assert json.loads(result['evidence']) == {
            'tipo': 'estatisticas_texto_local_v1', 'caracteres': 18,
            'palavras': 4, 'linhas': 2, 'bytes_utf8': 19}
        assert q.get(legacy['id'])['status'] == 'em_andamento'
    finally:
        worker.stop()
    assert Queue(directory, 'local_operator').get(task['id']) == result
    assert [e['action'] for e in q.audit() if e['task_id'] == task['id']] == ['criada', 'reivindicada', 'concluida']


def test_crash_recovery_and_pending_resume_in_new_process(directory):
    q = Queue(directory, 'local_operator')
    crashed = q.create_text_stats('inflight', priority=10, consent=True)
    pending = q.create_text_stats('waiting', consent=True)
    code = '''import os, sys, time\nfrom caiocore.task_queue import Queue\nfrom caiocore.task_queue.worker import LocalTextWorker\ndef crash(text): os._exit(23)\nw=LocalTextWorker(Queue(sys.argv[1], "local_operator"), interval=.01, processor=crash)\nw.start()\ntime.sleep(10)\n'''
    result = subprocess.run([sys.executable, '-c', code, str(directory)], timeout=15, capture_output=True, text=True)
    assert result.returncode == 23, result.stderr
    assert q.get(crashed['id'])['status'] == 'em_andamento'
    assert q.get(pending['id'])['status'] == 'pendente'
    w = LocalTextWorker(Queue(directory, 'local_operator'), interval=.01)
    w.start()
    try:
        wait_for(lambda: q.get(pending['id'])['status'] == 'concluida')
        blocked = q.get(crashed['id'])
        assert blocked['status'] == 'bloqueada' and 'desconhecido' in blocked['reason']
        assert [e['action'] for e in q.audit() if e['task_id'] == crashed['id']] == ['criada', 'reivindicada', 'recuperada']
    finally:
        w.stop()


def test_exception_is_failure_and_persistence_failure_blocks_after_restart(directory, monkeypatch):
    q = Queue(directory, 'local_operator')
    first = q.create_text_stats('exception', consent=True)
    def fail(_):
        raise RuntimeError('private exception never persisted')
    w = LocalTextWorker(q, processor=fail, interval=.01)
    w.start()
    try:
        wait_for(lambda: q.get(first['id'])['status'] == 'falhou')
        assert 'private' not in str(q.get(first['id']))
    finally:
        w.stop()
    next_task = q.create_text_stats('disk error', consent=True)
    original = q.complete
    def disk_error(*a, **kw):
        raise sqlite3.OperationalError('secret')
    monkeypatch.setattr(q, 'complete', disk_error)
    w2 = LocalTextWorker(q, interval=.01)
    w2.start()
    wait_for(lambda: not w2.running)
    assert w2.error and 'secret' not in w2.error
    assert q.get(next_task['id'])['status'] == 'em_andamento'
    w2.stop()
    monkeypatch.setattr(q, 'complete', original)
    w3 = LocalTextWorker(q, interval=.01)
    w3.start()
    try:
        assert q.get(next_task['id'])['status'] == 'bloqueada'
    finally:
        w3.stop()


def test_exclusive_lock_does_not_recover_live_work(directory):
    import threading
    q = Queue(directory, 'local_operator')
    item = q.create_text_stats('live', consent=True)
    entered, release = threading.Event(), threading.Event()
    def slow(text):
        entered.set()
        assert release.wait(5)
        return 'real local result'
    a = LocalTextWorker(q, processor=slow, interval=.01)
    a.start()
    try:
        assert entered.wait(5)
        assert q.get(item['id'])['status'] == 'em_andamento'
        with pytest.raises(InvalidTransition):
            q.cancel_waiting(item['id'])
        b = LocalTextWorker(Queue(directory, 'local_operator'))
        with pytest.raises(RuntimeError, match='already running'):
            b.start()
        assert q.get(item['id'])['status'] == 'em_andamento'
    finally:
        release.set()
        a.stop()
    assert q.get(item['id'])['status'] == 'concluida'


def test_api_lifecycle_consent_and_isolation(directory, monkeypatch):
    token = 'x' * 40
    monkeypatch.setenv('CAIO_API_TOKEN', token)
    monkeypatch.setenv('CAIO_LOCAL_QUEUE_ENABLED', '1')
    monkeypatch.setenv('CAIO_LOCAL_TEXT_WORKER_ENABLED', '1')
    monkeypatch.setenv('CAIO_LOCAL_QUEUE_DIR', str(directory))
    monkeypatch.setattr(api, '_local_queue_server_loopback', True)
    headers = {'authorization': 'Bearer ' + token}
    with TestClient(api.app, base_url='http://127.0.0.1', client=('127.0.0.1', 5)) as c:
        route = '/api/local-queue/text-statistics'
        assert c.get('/api/local-queue/worker', headers=headers).json()['running'] is True
        for payload in ({'text':'abc'}, {'text':'abc', 'consent':False}, {'text':'abc', 'consent':1},
                        {'text':'abc', 'consent':True, 'owner':'other'}, {'text':'abc', 'consent':True, 'tools':['shell']}):
            assert c.post(route, headers=headers, json=payload).status_code == 422
        assert c.post(route, json={'text':'abc', 'consent':True}).status_code == 401
        assert c.post(route, headers={**headers, 'x-forwarded-for':'127.0.0.1'}, json={'text':'abc','consent':True}).status_code == 403
        response = c.post(route, headers=headers, json={'text':'Um dois', 'consent':True})
        assert response.status_code == 201
        task_id = response.json()['id']
        result = wait_for(lambda: (x if (x := c.get('/api/local-queue/tasks/' + task_id, headers=headers).json())['status'] == 'concluida' else None))
        assert json.loads(result['evidence'])['palavras'] == 2
        assert 'owner' not in result and 'claim_token' not in result
        legacy = c.post('/api/local-queue/tasks', headers=headers, json={'description':'do not execute'}).json()
        time.sleep(.1)
        assert c.get('/api/local-queue/tasks/'+legacy['id'], headers=headers).json()['status'] == 'pendente'
    assert api._local_text_worker is None


def test_worker_disabled_without_loopback_or_optin(directory, monkeypatch):
    monkeypatch.setenv('CAIO_API_TOKEN', 'x'*40)
    monkeypatch.setenv('CAIO_LOCAL_QUEUE_ENABLED', '1')
    monkeypatch.setenv('CAIO_LOCAL_TEXT_WORKER_ENABLED', '1')
    monkeypatch.setenv('CAIO_LOCAL_QUEUE_DIR', str(directory))
    monkeypatch.setattr(api, '_local_queue_server_loopback', False)
    monkeypatch.setattr(api, '_local_text_worker', None)
    api._start_local_text_worker()
    assert api._local_text_worker is None and not list(directory.iterdir())
