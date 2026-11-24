"""Offline regressions for service lifetime lock and session-save races."""
import os
import subprocess
import sys

import pytest

from test_google_calendar_service_offline import authority_setup, no_network, state, TOKEN_URL, REVOKE_URL
from caiocore.server.google_calendar_service import GoogleCalendarService, ServiceError
from caiocore.server import google_calendar_service as module
from caiocore.server.google_session_authority import SessionError


def test_other_instance_rejected_and_disconnect_cancels_pending(authority_setup):
    service, fake, auth, path = authority_setup
    pending = state(service)
    with pytest.raises(ServiceError):
        GoogleCalendarService(path, auth, transport=fake)
    service.disconnect(service._test_owner)
    with pytest.raises(ServiceError):
        service.callback(service._test_owner, [('state', pending), ('code', 'fake-code')])
    assert not fake.calls
    assert not (path / 'google-token.json').exists()


def test_exclusive_lock_across_processes_and_released_on_close(authority_setup):
    service, _, _, path = authority_setup
    script = '''
import sys
from caiocore.server.google_calendar_service import GoogleCalendarService, ServiceError
from caiocore.server.google_session_authority import CalendarSessionAuthority
try:
    s = GoogleCalendarService(sys.argv[1], CalendarSessionAuthority())
except ServiceError:
    sys.exit(7)
s.close()
'''
    def child():
        return subprocess.run([sys.executable, '-c', script, str(path)], timeout=10, capture_output=True).returncode
    assert child() == 7
    service.close()
    assert child() == 0


def test_unavailable_posix_lock_fails_closed(authority_setup, monkeypatch):
    service, fake, auth, path = authority_setup
    service.close()
    monkeypatch.setattr(module, 'fcntl', None)
    with pytest.raises(ServiceError):
        GoogleCalendarService(path, auth, transport=fake)


def test_unsafe_lock_file_refused(authority_setup):
    service, fake, auth, path = authority_setup
    service.close()
    lock = path / 'calendar-service.lock'
    lock.chmod(0o644)
    with pytest.raises(ServiceError):
        GoogleCalendarService(path, auth, transport=fake)


def test_revocation_during_save_removes_local_and_revokes(authority_setup, monkeypatch):
    service, fake, auth, path = authority_setup
    pending = state(service)
    save = service._store.save
    def revoke_during_save(record):
        save(record)
        # A reentrant revoke fails immediately and poisons the outer commit,
        # even when malicious/injected code catches the exception.
        with pytest.raises(SessionError):
            auth.revoke(service._test_owner)
    monkeypatch.setattr(service._store, 'save', revoke_during_save)
    with pytest.raises(ServiceError, match='local removal confirmed; Google revocation confirmed'):
        service.callback(service._test_owner, [('state', pending), ('code', 'fake-code')])
    assert not (path / 'google-token.json').exists()
    assert [call[1] for call in fake.calls] == [TOKEN_URL, REVOKE_URL]


def test_failure_after_replace_removes_local_even_if_remote_revoke_fails(authority_setup, monkeypatch):
    service, fake, _, path = authority_setup
    pending = state(service)
    fake.fail_revoke = True
    save = service._store.save
    def partial_save(record):
        save(record)
        raise OSError('after replacement')
    monkeypatch.setattr(service._store, 'save', partial_save)
    with pytest.raises(ServiceError, match='local removal confirmed; Google revocation unconfirmed'):
        service.callback(service._test_owner, [('state', pending), ('code', 'fake-code')])
    assert not (path / 'google-token.json').exists()
    assert (path / 'calendar-transaction.pending').exists()
    with pytest.raises(ServiceError): service.status(service._test_owner)


def test_local_cleanup_failure_is_reported(authority_setup, monkeypatch):
    service, fake, auth, path = authority_setup
    pending = state(service)
    save = service._store.save
    def revoke_during_save(record):
        save(record)
        auth.revoke(service._test_owner)
    def fail_delete():
        raise OSError('test')
    monkeypatch.setattr(service._store, 'save', revoke_during_save)
    monkeypatch.setattr(service._store, 'delete_local', fail_delete)
    with pytest.raises(ServiceError, match='local removal unconfirmed; Google revocation confirmed'):
        service.callback(service._test_owner, [('state', pending), ('code', 'fake-code')])
    assert (path / 'google-token.json').exists()
    assert (path / 'calendar-transaction.pending').exists()
    with pytest.raises(ServiceError): service.status(service._test_owner)


def test_revocation_after_initial_check_before_commit_must_not_succeed(authority_setup, monkeypatch):
    service, fake, auth, path = authority_setup
    pending = state(service)
    owner = service._owner
    def check_then_revoke(ctx):
        result = owner(ctx)
        auth.revoke(ctx)
        return result
    monkeypatch.setattr(service, '_owner', check_then_revoke)
    with pytest.raises(ServiceError, match='local removal confirmed; Google revocation confirmed'):
        service.callback(service._test_owner, [('state', pending), ('code', 'fake-code')])
    assert [call[1] for call in fake.calls] == [TOKEN_URL, REVOKE_URL]
    assert not (path / 'google-token.json').exists()
    assert not (path / 'calendar-transaction.pending').exists()
    with pytest.raises(ServiceError): service.status(service._test_owner)
