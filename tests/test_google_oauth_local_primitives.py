import base64
import hashlib
import os
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import parse_qs, urlparse

import pytest

from caiocore.server.google_oauth_local import (
    AUTH_URL, REDIRECT_URI, SCOPES, OAuthError, PendingAuthorizations,
    PrivateTokenStore, accept_token_response,
)

SESSION = b'a' * 32  # Synthetic server-side session, not real credentials.
OTHER = b'b' * 32


def params(url):
    return {k: v[0] for k, v in parse_qs(urlparse(url).query).items()}


def result(scopes=None):
    return {'access_token': 'fake-access', 'refresh_token': 'fake-refresh',
            'token_type': 'Bearer', 'scope': ' '.join(scopes or [SCOPES['calendar_read']])}


def record():
    return {'refresh_token': 'fake-refresh', 'scopes': [SCOPES['calendar_read']]}


def test_flow_has_fixed_redirect_unique_state_pkce_and_opt_in():
    p = PendingAuthorizations('fake-client')
    u = p.start(SESSION, ['calendar_read'])
    q = params(u)
    q2 = params(p.start(SESSION, ['calendar_read']))
    assert u.startswith(AUTH_URL + '?')
    assert q['redirect_uri'] == REDIRECT_URI
    assert q['state'] != q2['state']
    assert q['scope'] == SCOPES['calendar_read']
    assert q['code_challenge_method'] == 'S256'
    verifier, scopes = p.consume(SESSION, q['state'])
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b'=').decode()
    assert q['code_challenge'] == challenge
    assert scopes == (SCOPES['calendar_read'],)
    with pytest.raises(OAuthError):
        p.consume(SESSION, q['state'])


def test_session_mismatch_does_not_consume():
    p = PendingAuthorizations('fake-client')
    state = params(p.start(SESSION, ['calendar_read']))['state']
    with pytest.raises(OAuthError):
        p.consume(OTHER, state)
    p.consume(SESSION, state)


def test_expiry_and_cancel():
    now = [0]
    p = PendingAuthorizations('fake-client', clock=lambda: now[0])
    state = params(p.start(SESSION, ['calendar_read']))['state']
    now[0] = 300
    with pytest.raises(OAuthError):
        p.consume(SESSION, state)
    now[0] = 301
    state = params(p.start(SESSION, ['gmail_read']))['state']
    p.cancel_all()
    with pytest.raises(OAuthError):
        p.consume(SESSION, state)


def test_concurrent_single_use():
    p = PendingAuthorizations('fake-client')
    state = params(p.start(SESSION, ['calendar_read']))['state']
    def use(_):
        try:
            p.consume(SESSION, state)
            return True
        except OAuthError:
            return False
    with ThreadPoolExecutor(max_workers=10) as pool:
        assert sum(pool.map(use, range(20))) == 1


@pytest.mark.parametrize('features', [[], ['unknown'], ['calendar_read', 'calendar_read'], 'calendar_read', [None]])
def test_invalid_scopes_fail_closed(features):
    with pytest.raises(OAuthError):
        PendingAuthorizations('fake-client').start(SESSION, features)


def test_invalid_session_and_state():
    p = PendingAuthorizations('fake-client')
    with pytest.raises(OAuthError):
        p.start(b'short', ['calendar_read'])
    for state in ('', 'x' * 43, '💩' * 43, None):
        with pytest.raises(OAuthError):
            p.consume(SESSION, state)


def test_response_requires_exact_granted_scopes_and_refresh():
    expected = (SCOPES['calendar_read'],)
    assert accept_token_response(result(), expected) == record()
    for bad in [
        {}, {**result(), 'refresh_token': ''}, {**result(), 'scope': ''},
        {**result(), 'scope': ' '.join([*expected, SCOPES['gmail_read']])},
        {**result(), 'scope': ' '.join([*expected, *expected])},
        {**result(), 'token_type': 'Other'},
    ]:
        with pytest.raises(OAuthError):
            accept_token_response(bad, expected)


@pytest.mark.skipif(os.name != 'posix', reason='POSIX storage only')
def test_store_atomic_mode_status_and_delete(tmp_path):
    tmp_path.chmod(0o700)
    with PrivateTokenStore(tmp_path) as store:
        assert store.status() == {'status': 'not_configured', 'scopes': []}
        store.save(accept_token_response(result(), (SCOPES['calendar_read'],)))
        f = tmp_path / 'google-token.json'
        assert f.stat().st_mode & 0o777 == 0o600
        assert store.load() == record()
        assert store.status() == {'status': 'stored_unverified', 'scopes': [SCOPES['calendar_read']]}
        tmp_path.chmod(0o755)
        with pytest.raises(OAuthError):
            store.load()
        tmp_path.chmod(0o700)
        assert 'fake-refresh' not in str(store.status())
        store.delete_local()
        store.delete_local()
        assert store.status()['status'] == 'not_configured'


@pytest.mark.skipif(os.name != 'posix', reason='POSIX storage only')
def test_insecure_directory_file_symlink_and_hardlink_fail(tmp_path):
    tmp_path.chmod(0o755)
    with pytest.raises(OAuthError):
        PrivateTokenStore(tmp_path)
    tmp_path.chmod(0o700)
    target = tmp_path / 'target'
    target.write_text('untouched')
    name = tmp_path / 'google-token.json'
    name.symlink_to(target)
    with PrivateTokenStore(tmp_path) as store:
        for operation in (store.load, store.delete_local, lambda: store.save(record())):
            with pytest.raises(OAuthError):
                operation()
        assert target.read_text() == 'untouched'
        name.unlink()
        target.chmod(0o600)
        os.link(target, name)
        with pytest.raises(OAuthError):
            store.load()
        name.unlink()
        target.rename(name)
        name.chmod(0o644)
        with pytest.raises(OAuthError):
            store.load()


@pytest.mark.skipif(os.name != 'posix', reason='POSIX storage only')
def test_invalid_record_and_corruption_fail(tmp_path):
    tmp_path.chmod(0o700)
    with PrivateTokenStore(tmp_path) as store:
        for invalid in ({}, {'refresh_token': 'fake', 'scopes': ['bad']},
                        {'refresh_token': '', 'scopes': [SCOPES['calendar_read']]}):
            with pytest.raises(OAuthError):
                store.save(invalid)
        name = tmp_path / 'google-token.json'
        name.write_text('{')
        name.chmod(0o600)
        with pytest.raises(OAuthError):
            store.status()
