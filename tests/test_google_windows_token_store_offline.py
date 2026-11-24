"""Offline Linux/Windows-independent tests; DPAPI itself is deliberately mocked."""
import hashlib
import hmac
import json
import os

import pytest

from caiocore.server import google_windows_token_store as module
from caiocore.server.google_oauth_local import OAuthError, SCOPES


class FakeDPAPI:
    """Authenticated test double, NOT an encryption implementation for production."""
    def protect(self, data):
        payload = bytes(b ^ 0xA5 for b in data)
        return hmac.digest(b'test-key', payload, 'sha256') + payload

    def unprotect(self, data):
        tag, payload = data[:32], data[32:]
        if not hmac.compare_digest(tag, hmac.digest(b'test-key', payload, 'sha256')):
            raise ValueError('bad tag')
        return bytes(b ^ 0xA5 for b in payload)


@pytest.fixture
def environment(monkeypatch, tmp_path):
    monkeypatch.setattr(module, '_is_windows', lambda: True)
    monkeypatch.setattr(module, '_local_app_data', lambda: tmp_path)
    monkeypatch.setattr(module, '_DPAPI', FakeDPAPI)
    return tmp_path


@pytest.fixture
def record():
    return {'refresh_token': 'fake-sensitive-refresh-token',
            'scopes': [SCOPES['calendar_read']]}


def test_not_windows_fails_closed(monkeypatch):
    monkeypatch.setattr(module, '_is_windows', lambda: False)
    with pytest.raises(OAuthError, match='unavailable'):
        module.WindowsTokenStore()


def test_absent_save_update_persist_status_delete(environment, record):
    directory = environment / 'Caio'
    with module.WindowsTokenStore(directory) as store:
        assert store.load() is None
        assert store.status() == {'status': 'not_configured', 'scopes': []}
        store.delete_local()
        store.save(record)
        file = directory / store._name
        assert b'fake-sensitive-refresh-token' not in file.read_bytes()
        assert store.load() == record
        assert store.status() == {'status': 'stored_unverified', 'scopes': record['scopes']}
        assert record['refresh_token'] not in str(store.status())
        changed = {**record, 'refresh_token': 'new-fake-secret'}
        store.save(changed)
        assert store.load() == changed
        assert not list(directory.glob('*.tmp'))
    with pytest.raises(OAuthError):
        store.load()
    with module.WindowsTokenStore() as reopened:
        assert reopened.load() == changed
        reopened.delete_local()
        reopened.delete_local()
        assert reopened.status() == {'status': 'not_configured', 'scopes': []}
        assert not file.exists()


def test_unavailable_dpapi_fails_even_if_absent(environment, monkeypatch, capsys):
    class Broken(FakeDPAPI):
        def protect(self, data):
            raise OSError('secret-detail-do-not-print')
    monkeypatch.setattr(module, '_DPAPI', Broken)
    with pytest.raises(OAuthError) as e:
        module.WindowsTokenStore()
    assert 'secret-detail' not in str(e.value)
    assert capsys.readouterr() == ('', '')
    assert not (environment / 'Caio').exists()


def test_disallow_arbitrary_directory(environment, tmp_path):
    with pytest.raises(OAuthError):
        module.WindowsTokenStore(tmp_path / 'elsewhere')


@pytest.mark.parametrize('invalid', [None, {}, {'refresh_token': '', 'scopes': [SCOPES['calendar_read']]},
    {'refresh_token': 'x', 'scopes': ['invalid']},
    {'refresh_token': 'x', 'scopes': [SCOPES['calendar_read']] * 2},
    {'refresh_token': 'x', 'scopes': [None]},
    {'refresh_token': 'x', 'scopes': [SCOPES['calendar_read']], 'extra': 'x'}])
def test_invalid_records_are_rejected(environment, invalid):
    with module.WindowsTokenStore() as store:
        with pytest.raises(OAuthError):
            store.save(invalid)
        assert store.load() is None


def test_tampering_corruption_refuses_load_save_delete(environment, record):
    with module.WindowsTokenStore() as store:
        store.save(record)
        path = environment / 'Caio' / store._name
        damaged = bytearray(path.read_bytes())
        damaged[-1] ^= 1
        path.write_bytes(damaged)
        for operation in (store.load, store.status, lambda: store.save(record), store.delete_local):
            with pytest.raises(OAuthError):
                operation()
        assert path.read_bytes() == damaged


@pytest.mark.parametrize('plaintext', [b'not-json', b'{"refresh_token":"a","refresh_token":"b","scopes":[]}',
                                      json.dumps({'refresh_token': 'x', 'scopes': ['bad']}).encode(),
                                      b'x' * (module._MAX_PLAIN + 1)])
def test_unprotected_bad_json_is_rejected(environment, plaintext):
    with module.WindowsTokenStore() as store:
        path = environment / 'Caio' / store._name
        path.write_bytes(FakeDPAPI().protect(plaintext))
        with pytest.raises(OAuthError):
            store.load()


def test_dpapi_runtime_errors_no_plaintext_fallback(environment, record, capsys):
    with module.WindowsTokenStore() as store:
        store.save(record)
        path = environment / 'Caio' / store._name
        original = path.read_bytes()
        class Broken(FakeDPAPI):
            def protect(self, data):
                raise ValueError('fake-sensitive-refresh-token')
        store._dpapi = Broken()
        with pytest.raises(OAuthError) as e:
            store.save(record)
        assert 'fake-sensitive-refresh-token' not in str(e.value)
        assert path.read_bytes() == original
        assert capsys.readouterr() == ('', '')


def test_io_failure_cleans_temporary_ciphertext(environment, record, monkeypatch):
    with module.WindowsTokenStore() as store:
        store.save(record)
        path = environment / 'Caio' / store._name
        original = path.read_bytes()
        def fail(*args):
            raise OSError('sensitive detail')
        monkeypatch.setattr(module.os, 'replace', fail)
        with pytest.raises(OAuthError):
            store.save({**record, 'refresh_token': 'replacement'})
        assert path.read_bytes() == original
        assert not list(path.parent.glob('*.tmp'))


def test_symlink_and_hardlink_rejected(environment, record):
    with module.WindowsTokenStore() as store:
        directory = environment / 'Caio'
        path = directory / store._name
        target = environment / 'target'
        target.write_bytes(b'untouched')
        try:
            path.symlink_to(target)
        except (OSError, NotImplementedError):
            pytest.skip('Symlinks unavailable')
        for operation in (store.load, lambda: store.save(record), store.delete_local):
            with pytest.raises(OAuthError):
                operation()
        assert target.read_bytes() == b'untouched'
        path.unlink()
        os.link(target, path)
        with pytest.raises(OAuthError):
            store.load()
        assert target.read_bytes() == b'untouched'


def test_missing_or_replaced_directory_not_reported_as_empty(environment):
    store = module.WindowsTokenStore()
    directory = environment / 'Caio'
    directory.rmdir()
    for operation in (store.load, store.status, store.delete_local):
        with pytest.raises(OAuthError):
            operation()
    directory.write_text('not a directory')
    with pytest.raises(OAuthError):
        store.load()
    store.close()


def test_close_and_oversize_cipher(environment, record):
    store = module.WindowsTokenStore()
    path = environment / 'Caio' / store._name
    path.write_bytes(b'x' * (module._MAX_CIPHER + 1))
    with pytest.raises(OAuthError):
        store.load()
    store.close()
    for op in (store.load, store.status, store.delete_local, lambda: store.save(record)):
        with pytest.raises(OAuthError):
            op()
