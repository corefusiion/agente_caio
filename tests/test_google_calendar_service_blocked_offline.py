"""Fail-closed quarantine tests: no session authority, no Google operations.

The legacy service tests document behavior that must not be enabled in production.
These tests exercise the actual public gate, with synthetic inputs and no network.
"""
from concurrent.futures import ThreadPoolExecutor
import threading

import pytest

from test_google_calendar_service_offline import setup, no_network
from caiocore.server.google_calendar_client import CALENDAR_SCOPE
from caiocore.server.google_calendar_service import ServiceError


def operations(service):
    return [
        lambda: service.start('owner'),
        lambda: service.callback('owner', [('state', 'synthetic'), ('code', 'synthetic')]),
        lambda: service.status('owner'),
        lambda: service.verify('owner'),
        lambda: service.list_events('owner'),
        lambda: service.disconnect('owner'),
    ]


@pytest.mark.parametrize('preexisting', [False, True])
def test_all_operations_are_blocked_before_io_and_preserve_storage(setup, monkeypatch, preexisting, capsys):
    service, fake, auth, path = setup
    if preexisting:
        service._store.save({'refresh_token': 'synthetic-refresh', 'scopes': [CALENDAR_SCOPE]})
    token = path / 'google-token.json'
    before = token.read_bytes() if preexisting else None
    invoked = []
    def forbidden(*args, **kwargs):
        invoked.append(True)
        raise AssertionError('unexpected underlying operation')
    monkeypatch.setattr(service, '_resolve', forbidden)
    monkeypatch.setattr(service._store, 'load', forbidden)
    monkeypatch.setattr(service._store, 'save', forbidden)
    monkeypatch.setattr(service._store, 'delete_local', forbidden)
    cancel_all = service._client.cancel_all
    monkeypatch.setattr(service._client, 'cancel_all', forbidden)
    for action in operations(service):
        with pytest.raises(ServiceError, match='Owner session transaction unavailable') as exc:
            action()
        assert 'synthetic' not in str(exc.value)
    assert not invoked and not fake.calls
    assert token.exists() is preexisting
    if preexisting:
        assert token.read_bytes() == before
    output = capsys.readouterr()
    assert 'synthetic' not in output.out + output.err
    # Fixture close requires the original cancel_all; keep network guard intact.
    monkeypatch.setattr(service._client, 'cancel_all', cancel_all)


def test_concurrent_callback_revocation_and_rotation_never_start(setup):
    service, fake, auth, path = setup
    service._store.save({'refresh_token': 'synthetic-refresh', 'scopes': [CALENDAR_SCOPE]})
    token = path / 'google-token.json'
    before = token.read_bytes()
    actions = operations(service)
    barrier = threading.Barrier(len(actions) + 1, timeout=5)
    def run(action):
        barrier.wait()
        with pytest.raises(ServiceError, match='Owner session transaction unavailable'):
            action()
    with ThreadPoolExecutor(max_workers=len(actions)) as pool:
        futures = [pool.submit(run, action) for action in actions]
        del auth['owner']  # Revocation precedes release of every operation.
        barrier.wait()
        for future in futures:
            future.result(timeout=5)
    assert token.read_bytes() == before
    assert not fake.calls


def test_revocation_while_callback_holds_service_lock_still_fails_closed(setup, monkeypatch):
    service, fake, auth, path = setup
    entered, resume = threading.Event(), threading.Event()
    owner = service._owner
    def paused_gate(context):
        entered.set()
        assert resume.wait(5)
        return owner(context)
    monkeypatch.setattr(service, '_owner', paused_gate)
    def callback():
        with pytest.raises(ServiceError, match='Owner session transaction unavailable'):
            service.callback('owner', [('state', 'synthetic'), ('code', 'synthetic')])
    with ThreadPoolExecutor(max_workers=2) as pool:
        pending = pool.submit(callback)
        try:
            assert entered.wait(5)
            del auth['owner']
            disconnect = pool.submit(service.disconnect, 'owner')
        finally:
            resume.set()
        pending.result(timeout=5)
        with pytest.raises(ServiceError, match='Owner session transaction unavailable'):
            disconnect.result(timeout=5)
    assert not fake.calls
    assert not (path / 'google-token.json').exists()


def test_concurrent_callbacks_cannot_exchange_or_save(setup):
    service, fake, _, path = setup
    barrier = threading.Barrier(3, timeout=5)
    def callback():
        barrier.wait()
        with pytest.raises(ServiceError, match='Owner session transaction unavailable'):
            service.callback('owner', [('state', 'synthetic'), ('code', 'synthetic')])
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(callback) for _ in range(2)]
        barrier.wait()
        for future in futures:
            future.result(timeout=5)
    assert not fake.calls
    assert not (path / 'google-token.json').exists()
