import concurrent.futures
import os
import sqlite3
import subprocess
import sys

import pytest

from caiocore.task_queue import Queue, InvalidTransition, StorageSecurityError


@pytest.fixture
def directory(tmp_path):
    path = tmp_path / 'queue'
    path.mkdir(mode=0o700)
    return path


def queue(directory, owner='alice'):
    return Queue(directory, owner)


def test_order_claim_and_persistence(directory):
    q = queue(directory)
    low = q.create('low', -10)
    high = q.create('high', 10)
    other = q.create('same', 10)
    assert [x['id'] for x in q.list()] == [high['id'], other['id'], low['id']]
    task, token = q.claim()
    assert task['id'] == high['id'] and task['status'] == 'em_andamento'
    assert 'claim_token' not in task and 'claim_token' not in q.get(task['id'])
    assert q.complete(task['id'], token, 'feito: recibo 123')['status'] == 'concluida'
    reopened = queue(directory)
    assert reopened.get(high['id'])['evidence'] == 'feito: recibo 123'
    assert [e['action'] for e in reopened.audit()] == ['criada', 'criada', 'criada', 'reivindicada', 'concluida']
    assert reopened.list(status='concluida')[0]['id'] == high['id']
    assert os.stat(directory / 'tasks.sqlite3').st_mode & 0o777 == 0o600


def test_scope_and_no_oracle(directory):
    alice, bob = queue(directory), queue(directory, 'bob')
    a = alice.create('alice secret')
    b = bob.create('bob secret')
    task, token = alice.claim()
    assert task['id'] == a['id']
    assert bob.get(a['id']) is None
    assert [x['id'] for x in bob.list()] == [b['id']]
    assert all(e['task_id'] == b['id'] for e in bob.audit())
    for operation in [lambda: bob.cancel(a['id']), lambda: bob.retry(a['id']),
                      lambda: bob.recover_after_crash(a['id']),
                      lambda: bob.complete(a['id'], token, 'proof'),
                      lambda: bob.fail(a['id'], token, 'fail'),
                      lambda: bob.block(a['id'], token, 'blocked')]:
        with pytest.raises(InvalidTransition, match='task unavailable'):
            operation()
    assert alice.get(a['id'])['status'] == 'em_andamento'


def test_cancel_claim_revocation_retry_and_failures(directory):
    q = queue(directory)
    a = q.create('a')
    task, token = q.claim()
    with pytest.raises(ValueError):
        q.complete(a['id'], token, '  ')
    with pytest.raises(InvalidTransition):
        q.complete(a['id'], 'f' * 32, 'proof')
    assert q.fail(a['id'], token, 'specific failure')['reason'] == 'specific failure'
    with pytest.raises(InvalidTransition):
        q.complete(a['id'], token, 'proof')
    assert q.retry(a['id'])['status'] == 'pendente'
    task, token2 = q.claim()
    assert token2 != token
    with pytest.raises(InvalidTransition):
        q.complete(a['id'], token, 'proof')
    assert q.block(a['id'], token2, 'need review')['reason'] == 'need review'
    q.retry(a['id'])
    _, token3 = q.claim()
    q.cancel(a['id'])
    with pytest.raises(InvalidTransition):
        q.complete(a['id'], token3, 'proof')
    with pytest.raises(InvalidTransition):
        q.retry(a['id'])
    assert q.claim() is None
    assert q.get(a['id'])['status'] == 'cancelada'
    for event in q.audit():
        assert set(event) == {'seq', 'task_id', 'action', 'old_status', 'new_status', 'at'}
        assert 'specific failure' not in str(event) and token3 not in str(event)


def test_pending_and_blocked_cancel_and_forbidden_transitions(directory):
    q = queue(directory)
    a, b = q.create('a'), q.create('b')
    for fn in (lambda: q.retry(a['id']), lambda: q.recover_after_crash(a['id']),
               lambda: q.complete(a['id'], '0'*32, 'proof')):
        with pytest.raises(InvalidTransition): fn()
    q.cancel(a['id'])
    task, token = q.claim()
    assert task['id'] == b['id']
    q.block(b['id'], token, 'aguarda')
    q.cancel(b['id'])
    with pytest.raises(InvalidTransition): q.cancel(b['id'])


def test_field_validation_and_boundaries(directory):
    q = queue(directory)
    assert len(q.create('a' * 4000)['description']) == 4000
    assert q.create('😀' * 4000, -10)['priority'] == -10
    assert q.create('x', 10)['priority'] == 10
    for s in ['', '  ', 'a'*4001, '\x00', '\ud800', 3, None]:
        with pytest.raises(ValueError): q.create(s)
    for p in [-11, 11, True, 1.5, '1', None]:
        with pytest.raises(ValueError): q.create('x', p)
    for own in ['', 'x'*65, '../test', 'ü', 'alice@example.com', None, 1]:
        with pytest.raises(ValueError): queue(directory, own)
    for bad in ['ABC', '../bad', 'g'*32, 'x'*33, None, 5]:
        with pytest.raises(ValueError): q.get(bad)
    for args in [{'status':'anything'}, {'limit':0}, {'limit':201}, {'limit':True}, {'offset':-1}, {'offset':True}]:
        with pytest.raises(ValueError): q.list(**args)
    for args in [{'after':-1}, {'after':2**63}, {'limit':201}]:
        with pytest.raises(ValueError): q.audit(**args)
    _, token = q.claim()
    # Claim output includes only public fields, and text limits apply to evidence/reasons.
    task = q.list(status='em_andamento')[0]
    for reason in ['', 'x'*2001, '\x00']:
        with pytest.raises(ValueError): q.fail(task['id'], token, reason)
    q.complete(task['id'], token, 'x'*2000)


def test_concurrent_claims_are_unique(directory):
    q = queue(directory)
    ids = {q.create(str(i), i % 3)['id'] for i in range(60)}
    def claim_one(_):
        return queue(directory).claim()
    with concurrent.futures.ThreadPoolExecutor(max_workers=16) as ex:
        claimed = list(ex.map(claim_one, range(75)))
    results = [v for v in claimed if v is not None]
    assert len(results) == 60
    assert {task['id'] for task, _ in results} == ids
    assert len({token for _, token in results}) == 60
    assert len([e for e in q.audit(limit=200) if e['action'] == 'reivindicada']) == 60


def test_crash_reopen_and_explicit_recovery(directory):
    q = queue(directory)
    original = q.create('potential external effect')
    code = '''import os, sys\nfrom caiocore.task_queue import Queue\nq=Queue(sys.argv[1], "alice")\ntask,token=q.claim()\nprint(token,flush=True)\nos._exit(23)\n'''
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE='1')
    result = subprocess.run([sys.executable, '-c', code, str(directory)], capture_output=True, text=True, env=env)
    assert result.returncode == 23, result.stderr
    token = result.stdout.strip()
    reopened = queue(directory)
    assert reopened.claim() is None
    assert reopened.get(original['id'])['status'] == 'em_andamento'
    recovered = reopened.recover_after_crash(original['id'])
    assert recovered['status'] == 'bloqueada' and 'desconhecido' in recovered['reason']
    with pytest.raises(InvalidTransition): reopened.complete(original['id'], token, 'late')
    reopened.retry(original['id'])
    assert reopened.claim()[0]['id'] == original['id']


def test_transaction_rollback_on_crash(directory):
    q = queue(directory)
    task = q.create('durable')
    code = '''import os, sqlite3, sys\ndb=sqlite3.connect(sys.argv[1], isolation_level=None)\ndb.execute("BEGIN IMMEDIATE")\ndb.execute("UPDATE tasks SET status='concluida' WHERE id=?", (sys.argv[2],))\nos._exit(12)\n'''
    p = subprocess.run([sys.executable, '-c', code, str(directory / 'tasks.sqlite3'), task['id']])
    assert p.returncode == 12
    assert queue(directory).get(task['id'])['status'] == 'pendente'


def test_private_directory_and_files_fail_closed(directory, tmp_path):
    directory.chmod(0o755)
    with pytest.raises(StorageSecurityError): queue(directory)
    directory.chmod(0o700)
    q = queue(directory)
    db = directory / 'tasks.sqlite3'
    db.chmod(0o644)
    with pytest.raises(StorageSecurityError): q.list()
    db.chmod(0o600)
    link = tmp_path / 'link'
    link.symlink_to(directory, target_is_directory=True)
    with pytest.raises(StorageSecurityError): queue(link)
    sidecar = directory / 'tasks.sqlite3-journal'
    sidecar.symlink_to(db)
    with pytest.raises(StorageSecurityError): q.list()
    sidecar.unlink()
    hardlink = directory / 'another'
    os.link(db, hardlink)
    with pytest.raises(StorageSecurityError): q.list()
    hardlink.unlink()
    assert q.list() == []


def test_db_symlink_and_unsafe_ancestor_rejected(tmp_path):
    outside = tmp_path / 'outside'
    outside.write_text('do not touch')
    d = tmp_path / 'private'
    d.mkdir(mode=0o700)
    (d / 'tasks.sqlite3').symlink_to(outside)
    with pytest.raises(StorageSecurityError): queue(d)
    assert outside.read_text() == 'do not touch'
    (d / 'tasks.sqlite3').unlink()
    parent = tmp_path / 'unsafe'
    parent.mkdir(mode=0o777)
    parent.chmod(0o777)
    nested = parent / 'private'
    nested.mkdir(mode=0o700)
    with pytest.raises(StorageSecurityError): queue(nested)
    with pytest.raises(StorageSecurityError): queue('relative/path')
    with pytest.raises(StorageSecurityError): queue(str(d) + '/../private')
