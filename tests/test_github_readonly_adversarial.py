import json
import socket

import pytest

from caiocore.github_readonly import GitHubReadOnly, GitHubReadOnlyError, HTTPSReadTransport
from caiocore.github_readonly import client as mod


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def blocked(*args, **kwargs):
        raise AssertionError('real network forbidden')
    monkeypatch.setattr(socket, 'socket', blocked)
    monkeypatch.setattr(socket, 'create_connection', blocked)


class Stub:
    def __init__(self, payload):
        self.payload = payload
        self.calls = 0

    def get(self, *args):
        self.calls += 1
        return 200, json.dumps(self.payload).encode()


def client(payload):
    return GitHubReadOnly([('Org', 'repo')], transport=Stub(payload))


def test_returned_text_cannot_echo_exact_runtime_token():
    token = 'secret_token_123'
    for payload, op in [
        ({'full_name': 'Org/repo', 'description': 'prefix ' + token}, 'repository'),
        ({'name': 'main', 'commit': {'sha': 'abc', 'html_url': token}}, 'branch'),
        ([{'sha': 'abc', 'commit': {'message': 'x' * 4097 + token}}], 'commits'),
    ]:
        obj = client(payload)
        with pytest.raises(GitHubReadOnlyError) as exc:
            if op == 'repository':
                obj.repository('Org', 'repo', token=token)
            else:
                getattr(obj, op)('Org', 'repo', 'main', token=token)
        assert token not in str(exc.value)


def test_mismatched_identity_is_rejected_but_github_casing_is_accepted():
    assert client({'full_name': 'org/Repo'}).repository('Org', 'repo')['full_name']['value'] == 'org/Repo'
    with pytest.raises(GitHubReadOnlyError):
        client({'full_name': 'Other/secret'}).repository('Org', 'repo')
    with pytest.raises(GitHubReadOnlyError):
        client({'name': 'other'}).branch('Org', 'repo', 'main')


def test_untrusted_control_characters_and_surrogates_are_replaced():
    obj = client({'full_name': 'Org/repo', 'description': 'hello\x1b[2J\r\u202e\ud800\nworld'})
    text = obj.repository('Org', 'repo')['description']
    assert text['value'] == 'hello\ufffd[2J\ufffd\ufffd\ufffd\nworld'
    assert text['sanitized'] is True and text['trust'] == 'untrusted'
    json.dumps(text, ensure_ascii=False).encode('utf-8')


@pytest.mark.parametrize('raw', [
    b'{"full_name":"Org/repo","full_name":"Other/secret"}',
    b'{"full_name":"Org/repo","nested":{"a":1,"a":2}}',
])
def test_ambiguous_json_rejected(raw):
    class Raw:
        def get(self, *args):
            return 200, raw
    with pytest.raises(GitHubReadOnlyError):
        GitHubReadOnly([('Org', 'repo')], transport=Raw()).repository('Org', 'repo')


@pytest.mark.parametrize('path', [
    '/repos/Org/repo/branches/topic%252Fone',
    '/repos/Org/repo/branches/topic%2fone',
    '/repos/Org/repo/commits?sha=main&per_page=20&page=1%26admin%3D1',
    '/repos/Org/repo/commits?sha=main&per_page=20&page=1&sha=other',
    '/repos/Org/repo/branches/%2e%2e%2fmain',
    '/repos/Org/repo/branches/main%0d%0aAuthorization%3Aevil',
])
def test_noncanonical_wire_paths_fail_before_connection(monkeypatch, path):
    called = []
    def forbidden(*args, **kwargs):
        called.append(True)
        raise AssertionError('connection attempted')
    monkeypatch.setattr(mod.http.client, 'HTTPSConnection', forbidden)
    with pytest.raises(GitHubReadOnlyError):
        HTTPSReadTransport().get(path, 'fake_runtime_token', 5, 262144)
    assert not called


def test_tls_does_not_enable_keylog_or_http_debug_from_environment(monkeypatch, tmp_path):
    import ssl
    keylog = tmp_path / 'tls-secrets.log'
    monkeypatch.setenv('SSLKEYLOGFILE', str(keylog))
    class Response:
        status = 200
        def read(self, limit):
            return b'{}'
    class Connection:
        debuglevel = 99  # simulate an unsafe process-wide class setting
        def __init__(self, host, timeout, context):
            assert host == 'api.github.com'
            assert context.verify_mode == ssl.CERT_REQUIRED
            assert context.check_hostname is True
            assert context.keylog_filename is None
        def request(self, method, path, headers):
            assert self.debuglevel == 0
            assert headers['Authorization'] == 'Bearer fake_runtime_token'
        def getresponse(self):
            return Response()
        def close(self):
            pass
    monkeypatch.setattr(mod.http.client, 'HTTPSConnection', Connection)
    assert HTTPSReadTransport().get('/repos/Org/repo', 'fake_runtime_token', 5, 262144) == (200, b'{}')
    assert not keylog.exists()
