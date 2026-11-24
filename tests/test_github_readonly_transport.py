import socket

import pytest

from caiocore.github_readonly import GitHubReadOnly, GitHubReadOnlyError, HTTPSReadTransport
from caiocore.github_readonly import client as mod


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def blocked(*args, **kwargs):
        raise AssertionError("real network is forbidden")
    monkeypatch.setattr(socket, "socket", blocked)
    monkeypatch.setattr(socket, "create_connection", blocked)


@pytest.mark.parametrize("path", ["/repos/Org/repo", "/repos/Org/repo/branches/topic%2Fone",
    "/repos/Org/repo/commits?sha=topic%2Fone&per_page=30&page=10"])
def test_valid_paths_through_transport_mocked_connection(monkeypatch, path):
    events = []
    class Response:
        status = 200
        def read(self, limit):
            events.append(limit)
            return b"{}"
    class Connection:
        def __init__(self, host, timeout, context):
            assert host == "api.github.com" and timeout == 5
        def request(self, method, received_path, headers):
            assert method == "GET" and received_path == path
        def getresponse(self):
            return Response()
        def close(self):
            events.append("closed")
    monkeypatch.setattr(mod.http.client, "HTTPSConnection", Connection)
    assert HTTPSReadTransport().get(path, None, 5, 262144) == (200, b"{}")
    assert events == [262145, "closed"]


def test_oversize_and_socket_failure_are_generic(monkeypatch):
    events = []
    class Response:
        status = 200
        def read(self, limit):
            assert limit == 262145
            return b"x" * limit
    class Connection:
        def __init__(self, host, timeout, context):
            pass
        def request(self, method, path, headers):
            pass
        def getresponse(self):
            return Response()
        def close(self):
            events.append("closed")
    monkeypatch.setattr(mod.http.client, "HTTPSConnection", Connection)
    with pytest.raises(GitHubReadOnlyError) as ex:
        GitHubReadOnly([("Org", "repo")], transport=HTTPSReadTransport()).repository("Org", "repo", token="secret_token")
    assert events == ["closed"] and "secret_token" not in str(ex.value)
    class Broken(Connection):
        def request(self, method, path, headers):
            raise OSError("secret_token private data")
    monkeypatch.setattr(mod.http.client, "HTTPSConnection", Broken)
    with pytest.raises(GitHubReadOnlyError) as ex:
        GitHubReadOnly([("Org", "repo")], transport=HTTPSReadTransport()).repository("Org", "repo", token="secret_token")
    assert "secret_token" not in str(ex.value) and "private" not in str(ex.value)
    assert events == ["closed", "closed"]
