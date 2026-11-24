import os
import sqlite3
import subprocess
import sys
import time

import pytest

from caiocore.task_queue import Queue, StorageSecurityError
from caiocore.task_queue.worker import LocalTextWorker


def test_v1_migration_preserves_legacy_and_never_runs_it(tmp_path):
    path = tmp_path / 'queue'
    path.mkdir(mode=0o700)
    dbpath = path / 'tasks.sqlite3'
    fd = os.open(dbpath, os.O_CREAT | os.O_EXCL | os.O_RDWR, 0o600)
    os.close(fd)
    db = sqlite3.connect(dbpath)
    db.executescript('''
    CREATE TABLE tasks (seq INTEGER PRIMARY KEY AUTOINCREMENT, owner TEXT NOT NULL, id TEXT NOT NULL,
      description TEXT NOT NULL, priority INTEGER NOT NULL, status TEXT NOT NULL, created_at TEXT NOT NULL,
      updated_at TEXT NOT NULL, claim_token TEXT, evidence TEXT, reason TEXT, UNIQUE(owner,id));
    CREATE TABLE audit (seq INTEGER NOT NULL, owner TEXT NOT NULL, task_id TEXT NOT NULL, action TEXT NOT NULL,
      old_status TEXT, new_status TEXT NOT NULL, at TEXT NOT NULL, PRIMARY KEY(owner,seq));
    CREATE INDEX queue_order ON tasks(owner,status,priority DESC,seq);
    CREATE INDEX audit_owner ON audit(owner,seq);
    PRAGMA user_version=1;
    ''')
    db.execute('INSERT INTO tasks(owner,id,description,priority,status,created_at,updated_at) VALUES(?,?,?,?,?,?,?)',
               ('local_operator', 'a'*32, 'old record', 1, 'pendente', 'then', 'then'))
    db.commit(); db.close()
    q = Queue(path, 'local_operator')
    assert q.get('a'*32)['kind'] == 'registro'
    w = LocalTextWorker(q, interval=.01)
    w.start()
    try:
        time.sleep(.1)
        assert q.get('a'*32)['status'] == 'pendente'
        assert q.claim_text_stats() is None
    finally:
        w.stop()
    with sqlite3.connect(dbpath) as db:
        assert db.execute('PRAGMA user_version').fetchone()[0] == 3


def test_exclusive_lock_across_processes_and_unsafe_lock_rejected(tmp_path):
    path = tmp_path / 'queue'
    path.mkdir(mode=0o700)
    q = Queue(path, 'local_operator')
    worker = LocalTextWorker(q)
    worker.start()
    try:
        code = '''import sys\nfrom caiocore.task_queue import Queue\nfrom caiocore.task_queue.worker import LocalTextWorker\nw=LocalTextWorker(Queue(sys.argv[1], 'local_operator'))\ntry: w.start()\nexcept RuntimeError: sys.exit(17)\nw.stop()\nsys.exit(0)\n'''
        p = subprocess.run([sys.executable, '-c', code, str(path)], capture_output=True, text=True, timeout=10)
        assert p.returncode == 17, p.stderr
    finally:
        worker.stop()
    (path / '.text-worker.lock').chmod(0o644)
    with pytest.raises(StorageSecurityError):
        LocalTextWorker(q).start()
