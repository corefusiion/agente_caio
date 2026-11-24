import sqlite3

import pytest

from caiocore.task_queue import Queue, StorageSecurityError


def test_audit_failure_rolls_back_create(tmp_path):
    path = tmp_path / 'queue'
    path.mkdir(mode=0o700)
    q = Queue(path, 'owner')
    with sqlite3.connect(path / 'tasks.sqlite3') as db:
        db.execute("CREATE TRIGGER reject_event BEFORE INSERT ON audit BEGIN SELECT RAISE(ABORT, 'audit unavailable'); END")
    with pytest.raises(sqlite3.IntegrityError):
        q.create('must not persist')
    assert q.list() == []
    assert q.audit() == []


def test_foreign_and_future_schema_rejected(tmp_path):
    for version in [0, 42]:
        path = tmp_path / f'q{version}'
        path.mkdir(mode=0o700)
        dbfile = path / 'tasks.sqlite3'
        dbfile.touch(mode=0o600)
        with sqlite3.connect(dbfile) as db:
            db.execute('CREATE TABLE foreign_table (id INTEGER)')
            db.execute(f'PRAGMA user_version={version}')
        with pytest.raises(StorageSecurityError):
            Queue(path, 'owner')
