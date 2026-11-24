import concurrent.futures
import json
import sqlite3
import time

import pytest
from fastapi.testclient import TestClient
from caiocore.server import api
from caiocore.task_queue import Queue
from caiocore.task_queue.worker import LocalTextWorker

TOKEN = 't' * 64
KEY = 'k' * 64
HEADERS = {'Authorization': 'Bearer ' + TOKEN, 'X-Caio-Local-Chat-Key': KEY}
PATH = '/api/chat/local-text-statistics'

@pytest.fixture
def env(monkeypatch, tmp_path):
    p = tmp_path / 'q'
    p.mkdir(mode=0o700)
    for k, v in {'CAIO_API_TOKEN': TOKEN, 'CAIO_LOCAL_QUEUE_ENABLED': '1',
                 'CAIO_LOCAL_TEXT_WORKER_ENABLED': '1', 'CAIO_LOCAL_CHAT_QUEUE_ENABLED': '1',
                 'CAIO_LOCAL_CHAT_QUEUE_KEY': KEY, 'CAIO_LOCAL_QUEUE_DIR': str(p)}.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setattr(api, '_local_queue_server_loopback', True)
    q = Queue(p, 'local_operator')
    w = LocalTextWorker(q, interval=.01)
    w.start()
    monkeypatch.setattr(api, '_local_text_worker', w)
    c = TestClient(api.app, base_url='http://127.0.0.1', client=('127.0.0.1', 9))
    yield c, q, p
    w.stop()


def payload(key='a'*32, text='um dois'):
    return {'request_id': key, 'text': text, 'consent': True}


def test_real_execution_reopen_replay_and_no_secrets(env):
    c, q, p = env
    r = c.post(PATH, headers=HEADERS, json=payload())
    assert r.status_code == 201, r.text
    tid = r.json()['task']['id']
    for _ in range(300):
        got = c.get(PATH + '/' + tid, headers=HEADERS)
        if got.json()['task']['status'] == 'concluida': break
        time.sleep(.01)
    assert got.json()['task']['status'] == 'concluida'
    assert json.loads(got.json()['task']['evidence'])['palavras'] == 2
    assert all(s not in got.text for s in (TOKEN, KEY, 'claim_token', 'owner'))
    assert got.headers['cache-control'] == 'no-store'
    again = c.post(PATH, headers=HEADERS, json=payload())
    assert again.status_code == 200 and not again.json()['created'] and again.json()['task']['id'] == tid
    assert c.post(PATH, headers=HEADERS, json=payload(text='different')).status_code == 409
    assert Queue(p, 'local_operator').get(tid)['status'] == 'concluida'
    assert len(q.list()) == 1
    assert [x['action'] for x in q.audit()] == ['criada', 'reivindicada', 'concluida']
    assert c.post(PATH, headers=HEADERS, json=payload('a'*32, 'um dois')).json()['task']['id'] == tid


def test_concurrency_idempotent(env):
    c, q, p = env
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as ex:
        rs = list(ex.map(lambda _: c.post(PATH, headers=HEADERS, json=payload('b'*32)), range(12)))
    assert sorted(r.status_code for r in rs) == [200]*11 + [201]
    assert len({r.json()['task']['id'] for r in rs}) == 1
    assert len(q.list()) == 1


def test_guards_forgery_and_isolation(env, monkeypatch):
    c, q, p = env
    other = Queue(p, 'other').create_text_stats('private', consent=True)
    assert c.get(PATH+'/'+other['id'], headers=HEADERS).status_code == 404
    assert c.post(PATH, json=payload()).status_code == 401
    assert c.post(PATH, headers={'Authorization':'Bearer '+TOKEN}, json=payload()).status_code == 401
    for h in ({'Origin':'http://127.0.0.1'}, {'Cookie':'a=b'}, {'Sec-Fetch-Site':'same-origin'},
              {'X-Forwarded-For':'127.0.0.1'}, {'Host':'evil.test'}):
        assert c.post(PATH, headers={**HEADERS, **h}, json=payload()).status_code == 403
    for body in (dict(payload(), owner='other'), dict(payload(), session_id='fake'), dict(payload(), tools=['shell']),
                 dict(payload(), consent=1), dict(payload(), consent=False), dict(payload(), priority=True),
                 dict(payload(), request_id='bad'), {'text':'x', 'consent':True}):
        assert c.post(PATH, headers=HEADERS, json=body).status_code == 422
    assert c.post(PATH, headers=HEADERS, content=b'{"text":"a","text":"b","consent":true}').status_code == 415
    assert c.post(PATH, headers={**HEADERS,'content-type':'application/json'}, content=b'{"text":"a","text":"b","consent":true}').status_code == 422
    assert c.post(PATH+'?session_id=foo', headers=HEADERS, json=payload()).status_code == 403
    assert q.list() == []
    monkeypatch.setenv('CAIO_LOCAL_CHAT_QUEUE_KEY', TOKEN)
    assert c.post(PATH, headers=HEADERS, json=payload()).status_code == 503


def test_restart_after_crash_keeps_idempotency(tmp_path):
    import subprocess
    import sys
    p = tmp_path / 'q'
    p.mkdir(mode=0o700)
    q = Queue(p, 'local_operator')
    first, created = q.create_chat_text_stats('c'*32, 'crash', consent=True)
    pending, _ = q.create_chat_text_stats('d'*32, 'pending', consent=True)
    code = '''import os,sys,time\nfrom caiocore.task_queue import Queue\nfrom caiocore.task_queue.worker import LocalTextWorker\ndef crash(_): os._exit(23)\nw=LocalTextWorker(Queue(sys.argv[1], 'local_operator'), interval=.01, processor=crash)\nw.start()\ntime.sleep(8)'''
    result = subprocess.run([sys.executable, '-c', code, str(p)], timeout=10)
    assert result.returncode == 23
    reopened = Queue(p, 'local_operator')
    w = LocalTextWorker(reopened, interval=.01)
    w.start()
    try:
        for _ in range(300):
            if reopened.get(pending['id'])['status'] == 'concluida': break
            time.sleep(.01)
        assert reopened.get(first['id'])['status'] == 'bloqueada'
        assert reopened.get(pending['id'])['status'] == 'concluida'
        replay, created = reopened.create_chat_text_stats('c'*32, 'crash', consent=True)
        assert not created and replay['id'] == first['id'] and replay['status'] == 'bloqueada'
        assert len(reopened.list()) == 2
    finally:
        w.stop()


def test_atomic_failure_no_task_or_key(env):
    c, q, p = env
    with sqlite3.connect(p / 'tasks.sqlite3') as db:
        db.execute("CREATE TRIGGER reject_chat BEFORE INSERT ON chat_requests BEGIN SELECT RAISE(ABORT, 'secret'); END")
    r = c.post(PATH, headers=HEADERS, json=payload())
    assert r.status_code == 503 and 'secret' not in r.text
    assert q.list() == [] and q.audit() == []
    with sqlite3.connect(p / 'tasks.sqlite3') as db:
        assert db.execute('SELECT count(*) FROM chat_requests').fetchone()[0] == 0
        db.execute('DROP TRIGGER reject_chat')
    assert c.post(PATH, headers=HEADERS, json=payload()).status_code == 201
