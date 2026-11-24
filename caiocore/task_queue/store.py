"""Transactional, owner-scoped local task storage; no authentication."""
from __future__ import annotations

import contextlib
import os
import re
import sqlite3
import stat
import uuid
from datetime import datetime, timezone


class QueueError(Exception):
    pass


class InvalidTransition(QueueError):
    """Absent, other-owner, stale claim, or invalid state (intentionally indistinguishable)."""


class StorageSecurityError(QueueError):
    pass


_OWNER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}\Z", re.ASCII)
_ID = re.compile(r"[0-9a-f]{32}\Z", re.ASCII)
_STATES = frozenset({"pendente", "em_andamento", "bloqueada", "concluida", "falhou", "cancelada"})
_DB = "tasks.sqlite3"


def _owner(value):
    if type(value) is not str or not _OWNER.fullmatch(value):
        raise ValueError("invalid owner identifier")
    return value


def _id(value):
    if type(value) is not str or not _ID.fullmatch(value):
        raise ValueError("invalid identifier")
    return value


def _text(value, limit):
    if type(value) is not str or not value.strip() or len(value) > limit:
        raise ValueError("invalid text")
    if any(ord(c) < 32 and c not in "\n\t" or 0x7f <= ord(c) <= 0x9f for c in value):
        raise ValueError("invalid text")
    try:
        if len(value.encode("utf-8")) > 4 * limit:
            raise ValueError("text too large")
    except UnicodeEncodeError as exc:
        raise ValueError("invalid text") from exc
    return value


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def _check_file(dirfd, name, required=False):
    try:
        st = os.stat(name, dir_fd=dirfd, follow_symlinks=False)
    except FileNotFoundError:
        if required:
            raise StorageSecurityError("database missing")
        return
    if (not stat.S_ISREG(st.st_mode) or st.st_uid != os.geteuid()
            or stat.S_IMODE(st.st_mode) != 0o600 or st.st_nlink != 1):
        raise StorageSecurityError("unsafe SQLite file: " + name)


@contextlib.contextmanager
def _secure_dir(path):
    # Reject symlinks in every component. Directory ownership and write permissions
    # are checked before SQLite opens a path; assumes same-UID processes are trusted.
    if os.name != "posix" or not hasattr(os, "O_NOFOLLOW") or not os.path.isdir("/proc/self/fd"):
        raise StorageSecurityError("requires POSIX and /proc/self/fd")
    raw = os.fspath(path)
    if not isinstance(raw, str) or not raw.startswith("/") or ".." in raw.split("/"):
        raise StorageSecurityError("directory must be an absolute non-traversing path")
    parts = [p for p in raw.split("/") if p and p != "."]
    if not parts:
        raise StorageSecurityError("private directory required")
    fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for index, part in enumerate(parts):
            try:
                child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            except OSError as exc:
                raise StorageSecurityError("unsafe or missing directory") from exc
            os.close(fd)
            fd = child
            st = os.fstat(fd)
            mode = stat.S_IMODE(st.st_mode)
            if index == len(parts) - 1:
                if st.st_uid != os.geteuid() or mode != 0o700:
                    raise StorageSecurityError("queue directory must be owned by current user and mode 0700")
            elif st.st_uid not in (0, os.geteuid()) or (mode & 0o022 and not (st.st_uid == 0 and mode & stat.S_ISVTX)):
                raise StorageSecurityError("unsafe parent directory")
        yield fd
    finally:
        os.close(fd)


class Queue:
    """Private storage for one owner; owner must originate in a trusted service."""

    def __init__(self, directory, owner):
        self.directory = os.fspath(directory)
        self.owner = _owner(owner)
        with self._connection() as db:
            version = db.execute("PRAGMA user_version").fetchone()[0]
            if version not in (0, 1, 2, 3):
                raise StorageSecurityError("unsupported schema version")
            if version == 0:
                if db.execute("SELECT 1 FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' LIMIT 1").fetchone():
                    raise StorageSecurityError("unrecognized database")
                db.execute("""CREATE TABLE IF NOT EXISTS tasks (
                    seq INTEGER PRIMARY KEY AUTOINCREMENT,
                    owner TEXT NOT NULL, id TEXT NOT NULL, description TEXT NOT NULL,
                    priority INTEGER NOT NULL, status TEXT NOT NULL,
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                    claim_token TEXT, evidence TEXT, reason TEXT,
                    UNIQUE(owner, id))""")
                db.execute("""CREATE TABLE IF NOT EXISTS audit (
                    seq INTEGER NOT NULL, owner TEXT NOT NULL,
                    task_id TEXT NOT NULL, action TEXT NOT NULL,
                    old_status TEXT, new_status TEXT NOT NULL, at TEXT NOT NULL,
                    PRIMARY KEY(owner, seq))""")
                db.execute("CREATE INDEX IF NOT EXISTS queue_order ON tasks(owner, status, priority DESC, seq)")
                db.execute("CREATE INDEX IF NOT EXISTS audit_owner ON audit(owner, seq)")
                db.execute("PRAGMA user_version=1")
            if version in (0, 1):
                # Existing registrations never become executable implicitly.
                db.execute("ALTER TABLE tasks ADD COLUMN kind TEXT NOT NULL DEFAULT 'registro'")
                db.execute("PRAGMA user_version=2")
            if version in (0, 1, 2):
                db.execute("""CREATE TABLE chat_requests (
                    owner TEXT NOT NULL, request_id TEXT NOT NULL,
                    task_id TEXT NOT NULL, fingerprint TEXT NOT NULL,
                    PRIMARY KEY(owner, request_id), UNIQUE(owner, task_id))""")
                db.execute("PRAGMA user_version=3")

    @contextlib.contextmanager
    def _connection(self):
        with _secure_dir(self.directory) as directory_fd:
            try:
                flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
                try:
                    fd = os.open(_DB, flags, 0o600, dir_fd=directory_fd)
                except FileExistsError:
                    pass
                else:
                    try:
                        os.fchmod(fd, 0o600)
                    finally:
                        os.close(fd)
                for suffix in ("", "-journal", "-wal", "-shm"):
                    _check_file(directory_fd, _DB + suffix, required=(suffix == ""))
                # mode=rw: SQLite must never create a missing database implicitly.
                db = sqlite3.connect(f"file:/proc/self/fd/{directory_fd}/{_DB}?mode=rw", uri=True,
                                     timeout=30, isolation_level=None)
                db.row_factory = sqlite3.Row
                try:
                    db.execute("PRAGMA busy_timeout=30000")
                    mode = db.execute("PRAGMA journal_mode=DELETE").fetchone()[0]
                    if mode.lower() != "delete":
                        raise StorageSecurityError("only rollback journal mode supported")
                    db.execute("PRAGMA synchronous=FULL")
                    db.execute("BEGIN IMMEDIATE")
                    try:
                        yield db
                        db.execute("COMMIT")
                    except BaseException:
                        db.execute("ROLLBACK")
                        raise
                finally:
                    db.close()
            except OSError as exc:
                raise StorageSecurityError("cannot securely open queue") from exc

    @staticmethod
    def _public(row):
        if row is None:
            return None
        result = dict(row)
        result.pop("claim_token", None)
        result.pop("seq", None)
        return result

    def _event(self, db, task_id, action, old, new, at):
        db.execute("""INSERT INTO audit(seq,owner,task_id,action,old_status,new_status,at)
                   VALUES((SELECT COALESCE(MAX(seq),0)+1 FROM audit WHERE owner=?),?,?,?,?,?,?)""",
                   (self.owner, self.owner, task_id, action, old, new, at))

    def create(self, description, priority=0):
        return self._create(description, priority, "registro")

    def create_text_stats(self, text, priority=0, *, consent=False):
        """Explicit per-task authorization for offline text statistics only."""
        if consent is not True:
            raise ValueError("explicit consent required")
        return self._create(text, priority, "estatisticas_texto_local_v1")

    def create_chat_text_stats(self, request_id, text, priority=0, *, consent=False):
        """Commit the deduplication key and work atomically for this owner."""
        import hashlib
        import json
        _id(request_id)
        text = _text(text, 4000)
        if consent is not True or type(priority) is not int or not -10 <= priority <= 10:
            raise ValueError('invalid authorization or priority')
        fingerprint = hashlib.sha256(json.dumps(
            ['estatisticas_texto_local_v1', text, priority, True], ensure_ascii=False,
            separators=(',', ':')).encode('utf-8')).hexdigest()
        with self._connection() as db:
            existing = db.execute('SELECT task_id, fingerprint FROM chat_requests WHERE owner=? AND request_id=?',
                                  (self.owner, request_id)).fetchone()
            if existing:
                if existing['fingerprint'] != fingerprint:
                    raise InvalidTransition('request already used')
                task = self._get(db, existing['task_id'])
                if task is None:
                    raise StorageSecurityError('request mapping inconsistent')
                return task, False
            task_id, now = uuid.uuid4().hex, _now()
            db.execute('INSERT INTO tasks(owner,id,description,priority,status,created_at,updated_at,kind) VALUES(?,?,?,?,?,?,?,?)',
                       (self.owner, task_id, text, priority, 'pendente', now, now, 'estatisticas_texto_local_v1'))
            self._event(db, task_id, 'criada', None, 'pendente', now)
            db.execute('INSERT INTO chat_requests(owner,request_id,task_id,fingerprint) VALUES(?,?,?,?)',
                       (self.owner, request_id, task_id, fingerprint))
            return self._get(db, task_id), True

    def _create(self, description, priority, kind):
        description = _text(description, 4000)
        if type(priority) is not int or not -10 <= priority <= 10:
            raise ValueError("priority must be an integer from -10 to 10")
        task_id, now = uuid.uuid4().hex, _now()
        with self._connection() as db:
            db.execute("INSERT INTO tasks(owner,id,description,priority,status,created_at,updated_at,kind) VALUES(?,?,?,?,?,?,?,?)",
                       (self.owner, task_id, description, priority, "pendente", now, now, kind))
            self._event(db, task_id, "criada", None, "pendente", now)
            return self._get(db, task_id)

    def _get(self, db, task_id):
        return self._public(db.execute("SELECT * FROM tasks WHERE owner=? AND id=?", (self.owner, task_id)).fetchone())

    def get(self, task_id):
        _id(task_id)
        with self._connection() as db:
            return self._get(db, task_id)

    def list(self, *, status=None, limit=100, offset=0):
        if status is not None and (type(status) is not str or status not in _STATES):
            raise ValueError("invalid status")
        if type(limit) is not int or not 1 <= limit <= 200 or type(offset) is not int or not 0 <= offset <= 1000000:
            raise ValueError("invalid pagination")
        with self._connection() as db:
            if status is None:
                rows = db.execute("SELECT * FROM tasks WHERE owner=? ORDER BY priority DESC, seq LIMIT ? OFFSET ?", (self.owner, limit, offset))
            else:
                rows = db.execute("SELECT * FROM tasks WHERE owner=? AND status=? ORDER BY priority DESC, seq LIMIT ? OFFSET ?", (self.owner, status, limit, offset))
            return [self._public(row) for row in rows]

    def audit(self, *, limit=100, after=0):
        if type(limit) is not int or not 1 <= limit <= 200 or type(after) is not int or not 0 <= after <= 2**63-1:
            raise ValueError("invalid pagination")
        with self._connection() as db:
            rows = db.execute("SELECT seq,task_id,action,old_status,new_status,at FROM audit WHERE owner=? AND seq>? ORDER BY seq LIMIT ?", (self.owner, after, limit))
            return [dict(row) for row in rows]

    def claim(self):
        """Claim only a non-executable registration."""
        return self._claim("registro")

    def claim_text_stats(self):
        """For the exclusive local worker only."""
        return self._claim("estatisticas_texto_local_v1")

    def _claim(self, kind):
        token, now = uuid.uuid4().hex, _now()
        with self._connection() as db:
            row = db.execute("SELECT id FROM tasks WHERE owner=? AND status='pendente' AND kind=? ORDER BY priority DESC, seq LIMIT 1", (self.owner, kind)).fetchone()
            if row is None:
                return None
            task_id = row[0]
            result = db.execute("UPDATE tasks SET status='em_andamento',claim_token=?,updated_at=? WHERE owner=? AND id=? AND status='pendente'", (token, now, self.owner, task_id))
            if result.rowcount != 1:
                raise InvalidTransition("task unavailable")
            self._event(db, task_id, "reivindicada", "pendente", "em_andamento", now)
            return self._get(db, task_id), token

    def _change(self, task_id, action, allowed, new, *, token=None, evidence=None, reason=None):
        _id(task_id)
        if token is not None:
            _id(token)
        now = _now()
        with self._connection() as db:
            row = db.execute("SELECT status,claim_token FROM tasks WHERE owner=? AND id=?", (self.owner, task_id)).fetchone()
            if row is None or row["status"] not in allowed or (token is not None and row["claim_token"] != token):
                raise InvalidTransition("task unavailable")
            old = row["status"]
            db.execute("UPDATE tasks SET status=?,claim_token=NULL,evidence=?,reason=?,updated_at=? WHERE owner=? AND id=?", (new, evidence, reason, now, self.owner, task_id))
            self._event(db, task_id, action, old, new, now)
            return self._get(db, task_id)

    def complete(self, task_id, token, evidence):
        _id(token)
        return self._change(task_id, "concluida", {"em_andamento"}, "concluida", token=token, evidence=_text(evidence, 2000))

    def fail(self, task_id, token, reason):
        _id(token)
        return self._change(task_id, "falhou", {"em_andamento"}, "falhou", token=token, reason=_text(reason, 2000))

    def block(self, task_id, token, reason):
        _id(token)
        return self._change(task_id, "bloqueada", {"em_andamento"}, "bloqueada", token=token, reason=_text(reason, 2000))

    def cancel(self, task_id):
        return self._change(task_id, "cancelada", {"pendente", "em_andamento", "bloqueada"}, "cancelada")

    def cancel_waiting(self, task_id):
        """Do not imply an already-running operation stopped."""
        return self._change(task_id, "cancelada", {"pendente", "bloqueada"}, "cancelada")

    def recover_text_stats(self):
        """Call only with exclusive worker lock after the prior worker stopped."""
        now = _now()
        with self._connection() as db:
            rows = db.execute("SELECT id FROM tasks WHERE owner=? AND status='em_andamento' AND kind=?", (self.owner, "estatisticas_texto_local_v1")).fetchall()
            for row in rows:
                db.execute("UPDATE tasks SET status='bloqueada',claim_token=NULL,reason=?,updated_at=? WHERE owner=? AND id=?", ("Execução interrompida; resultado desconhecido.", now, self.owner, row["id"]))
                self._event(db, row["id"], "recuperada", "em_andamento", "bloqueada", now)
            return len(rows)

    def retry(self, task_id):
        """Explicit operator action, not proof external effects can be repeated safely."""
        return self._change(task_id, "reiniciada", {"falhou", "bloqueada"}, "pendente")

    def recover_after_crash(self, task_id):
        """Explicitly block one in-flight task after ALL its workers have been stopped."""
        return self._change(task_id, "recuperada", {"em_andamento"}, "bloqueada", reason="Execução interrompida; resultado externo desconhecido.")
