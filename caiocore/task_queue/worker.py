"""Exclusive opt-in local worker. No agent, tools, filesystem work, or network."""
from __future__ import annotations

import fcntl
import json
import os
import re
import threading

from .store import StorageSecurityError, _check_file, _secure_dir

_LOCK = '.text-worker.lock'


def text_statistics(text):
    """Deterministic local statistics; a description is data, never instructions."""
    return json.dumps({
        'tipo': 'estatisticas_texto_local_v1',
        'caracteres': len(text),
        'palavras': len(re.findall(r'\b\w+\b', text, flags=re.UNICODE)),
        'linhas': len(text.splitlines()),
        'bytes_utf8': len(text.encode('utf-8')),
    }, ensure_ascii=False, separators=(',', ':'))


class LocalTextWorker:
    """One lock per directory, held until the processing thread has terminated.

    Same-UID processes and callers of queue internals are trusted. Never use a
    second executor or change/delete the lock file while this worker runs.
    """
    def __init__(self, queue, *, interval=0.25, processor=text_statistics):
        self.queue = queue
        self.interval = interval
        self.processor = processor
        self._stop = threading.Event()
        self._thread = None
        self._fd = None
        self.error = None  # generic, never contains input or secrets

    @property
    def running(self):
        return self._thread is not None and self._thread.is_alive()

    def start(self):
        if self._thread is not None:
            raise RuntimeError('worker already started')
        with _secure_dir(self.queue.directory) as dirfd:
            flags = os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW
            fd = os.open(_LOCK, flags, 0o600, dir_fd=dirfd)
            try:
                _check_file(dirfd, _LOCK, required=True)
                st = os.fstat(fd)
                # Compare path and opened inode; all same-UID processes are trusted.
                current = os.stat(_LOCK, dir_fd=dirfd, follow_symlinks=False)
                if (st.st_dev, st.st_ino) != (current.st_dev, current.st_ino):
                    raise StorageSecurityError('unsafe worker lock')
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError as exc:
                    raise RuntimeError('local worker already running') from exc
                # Lock proves no prior cooperating worker is still running.
                self.queue.recover_text_stats()
                self._fd = fd
                self._thread = threading.Thread(target=self._run, name='caio-local-text-worker', daemon=True)
                self._thread.start()
            except BaseException:
                os.close(fd)
                self._fd = None
                raise

    def _run(self):
        try:
            while not self._stop.is_set():
                claim = self.queue.claim_text_stats()
                if claim is None:
                    self._stop.wait(self.interval)
                    continue
                task, token = claim
                try:
                    result = self.processor(task['description'])
                except Exception:
                    # The processor is pure local. Don't persist exception messages.
                    self.queue.fail(task['id'], token, 'Falha no processamento local de texto.')
                else:
                    self.queue.complete(task['id'], token, result)
        except Exception:
            # Persistence failure or stale claim: halt, do not claim more work.
            # The interrupted claim remains for recovery on the next start.
            self.error = 'Worker interrompido por erro de armazenamento ou estado.'
        finally:
            os.close(self._fd)
            self._fd = None

    def stop(self):
        self._stop.set()
        if self._thread is not None:
            # Wait for any in-progress computation and persistence; never promise
            # cancellation of work already claimed.
            self._thread.join()
