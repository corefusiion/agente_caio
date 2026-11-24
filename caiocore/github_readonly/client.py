"""Narrow GitHub REST client. No transport is enabled implicitly."""
from __future__ import annotations

import http.client
import json
import re
import ssl
import unicodedata
from urllib.parse import parse_qsl, quote, unquote, urlencode, urlsplit

_HOST = "api.github.com"
_TIMEOUT = 5.0  # socket operation timeout, not an end-to-end deadline
_MAX_BYTES = 256 * 1024
_MAX_TEXT = 4096
_OWNER = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?\Z", re.ASCII)
_REPO = re.compile(r"[A-Za-z0-9_.-]{1,100}\Z", re.ASCII)
_BRANCH = re.compile(r"[A-Za-z0-9_./-]{1,200}\Z", re.ASCII)
_TOKEN = re.compile(r"[A-Za-z0-9_-]{1,512}\Z", re.ASCII)
# The transport independently rejects paths outside these three GET endpoints.
_WIRE_PATH = re.compile(
    r"/repos/[A-Za-z0-9-]{1,39}/[A-Za-z0-9_.-]{1,100}"
    r"(?:/branches/[A-Za-z0-9_.%/-]{1,600}|/commits(?:\?sha=[A-Za-z0-9_.%+-]{1,600}&per_page=[0-9]{1,2}&page=[0-9]{1,2})?)?\Z",
    re.ASCII,
)


class GitHubReadOnlyError(Exception):
    """Deliberately generic: never carries server data or credentials."""


def _fail():
    raise GitHubReadOnlyError("Operação GitHub somente leitura indisponível ou inválida.") from None


def _valid_repo(owner, repo):
    return (type(owner) is str and type(repo) is str and bool(_OWNER.fullmatch(owner))
            and "--" not in owner and bool(_REPO.fullmatch(repo))
            and ".." not in repo and repo not in (".", ".."))


def _valid_branch(branch):
    if type(branch) is not str or not _BRANCH.fullmatch(branch):
        return False
    parts = branch.split("/")
    return (branch != "@" and ".." not in branch and not branch.endswith(".")
            and all(p and not p.startswith(".") and not p.lower().endswith(".lock") for p in parts))


def _valid_token(token):
    return token is None or (type(token) is str and bool(_TOKEN.fullmatch(token)))


def _valid_wire_path(path):
    """Reject noncanonical, redirected, traversal and extra-parameter URLs."""
    if type(path) is not str or not _WIRE_PATH.fullmatch(path):
        return False
    try:
        parsed = urlsplit(path)
        parts = parsed.path.split("/")
        if parsed.scheme or parsed.netloc or parsed.fragment or len(parts) < 4:
            return False
        if parts[1] != "repos" or not _valid_repo(parts[2], parts[3]):
            return False
        if len(parts) == 4:
            return not parsed.query
        if len(parts) == 6 and parts[4] == "branches" and not parsed.query:
            branch = unquote(parts[5], errors="strict")
            return _valid_branch(branch) and quote(branch, safe="") == parts[5]
        if len(parts) == 5 and parts[4] == "commits":
            pairs = parse_qsl(parsed.query, keep_blank_values=True, strict_parsing=True)
            if len(pairs) != 3 or [key for key, _ in pairs] != ["sha", "per_page", "page"]:
                return False
            branch, per_page, page = [value for _, value in pairs]
            return (_valid_branch(branch) and per_page.isascii() and per_page.isdigit()
                    and page.isascii() and page.isdigit() and 1 <= int(per_page) <= 30
                    and 1 <= int(page) <= 10 and urlencode(pairs) == parsed.query)
    except (ValueError, UnicodeError):
        return False
    return False


class HTTPSReadTransport:
    """Opt-in stdlib transport: fixed HTTPS host, GET only, no redirect following.

    Callers must trust any substitute transport; it receives the runtime token.
    """

    def get(self, path, token, timeout, max_bytes):
        if (not _valid_wire_path(path) or not _valid_token(token)
                or timeout != _TIMEOUT or max_bytes != _MAX_BYTES):
            _fail()
        headers = {"Accept": "application/vnd.github+json", "User-Agent": "Caio-GitHub-ReadOnly",
                   "X-GitHub-Api-Version": "2022-11-28"}
        if token is not None:
            headers["Authorization"] = "Bearer " + token
        conn = None
        failed = False
        result = None
        try:
            # Construct directly rather than create_default_context(): the latter
            # enables TLS key logging when SSLKEYLOGFILE is set in the environment.
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
            context.load_default_certs()
            conn = http.client.HTTPSConnection(_HOST, timeout=_TIMEOUT, context=context)
            # Never let a process-wide HTTPConnection debuglevel print headers.
            conn.debuglevel = 0
            conn.request("GET", path, headers=headers)
            response = conn.getresponse()
            # No redirects are followed; do not read error bodies.
            if response.status != 200:
                failed = True
            else:
                body = response.read(_MAX_BYTES + 1)
                if len(body) > _MAX_BYTES:
                    failed = True
                else:
                    result = (response.status, body)
        except Exception:
            failed = True
        finally:
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    failed = True
        if failed:
            _fail()
        return result


def _text(value, token):
    if type(value) is not str:
        return None
    # A faulty server or trusted transport might echo a runtime credential.
    if token is not None and token in value:
        _fail()
    # Replacing visual controls makes display safer, not LLM instructions safe.
    clipped = value[:_MAX_TEXT]
    safe = "".join("\ufffd" if (unicodedata.category(c) in ("Cc", "Cf", "Cs")
                               and c not in "\n\t") else c for c in clipped)
    result = {"value": safe, "trust": "untrusted", "truncated": len(value) > _MAX_TEXT}
    if safe != clipped:
        result["sanitized"] = True
    return result


def _add_text(out, dest, obj, key, token):
    value = _text(obj.get(key), token)
    if value is not None:
        out[dest] = value


def _repository(obj, owner, repo, token):
    if (type(obj) is not dict or type(obj.get("full_name")) is not str
            or obj["full_name"].lower() != (owner + "/" + repo).lower()):
        _fail()
    out = {}
    for key in ("name", "full_name", "description", "default_branch", "html_url"):
        _add_text(out, key, obj, key, token)
    if type(obj.get("private")) is bool:
        out["private"] = obj["private"]
    return out


def _commit(obj, token):
    if type(obj) is not dict or type(obj.get("sha")) is not str:
        _fail()
    out = {}
    _add_text(out, "sha", obj, "sha", token)
    _add_text(out, "html_url", obj, "html_url", token)
    info = obj.get("commit")
    if type(info) is dict:
        _add_text(out, "message", info, "message", token)
        author = info.get("author")
        if type(author) is dict:
            _add_text(out, "author_name", author, "name", token)
            _add_text(out, "author_date", author, "date", token)
    return out


def _unique_object(pairs):
    obj = {}
    for key, value in pairs:
        if key in obj:
            raise ValueError("duplicate JSON key")
        obj[key] = value
    return obj


class GitHubReadOnly:
    """Explicitly allowlisted repositories; only repo, branch, and commits reads.

    transport is required for operations. Pass HTTPSReadTransport() explicitly
    to enable network, or inject a trusted test double for offline tests.
    """

    def __init__(self, allowed_repositories, *, transport=None):
        if type(allowed_repositories) not in (list, tuple, set, frozenset):
            _fail()
        if not allowed_repositories or len(allowed_repositories) > 100:
            _fail()
        allowed = set()
        for pair in allowed_repositories:
            if type(pair) not in (tuple, list) or len(pair) != 2 or not _valid_repo(*pair):
                _fail()
            allowed.add((pair[0], pair[1]))
        self._allowed = frozenset(allowed)
        self._transport = transport

    def _base(self, owner, repo):
        if not _valid_repo(owner, repo) or (owner, repo) not in self._allowed:
            _fail()
        return "/repos/" + owner + "/" + repo

    def _get(self, path, token):
        if not _valid_token(token) or self._transport is None:
            _fail()
        failed = False
        response = None
        try:
            response = self._transport.get(path, token, _TIMEOUT, _MAX_BYTES)
        except Exception:
            failed = True
        if failed:
            _fail()
        if (type(response) is not tuple or len(response) != 2 or type(response[0]) is not int
                or response[0] != 200 or type(response[1]) is not bytes
                or len(response[1]) > _MAX_BYTES):
            _fail()
        failed = False
        obj = None
        try:
            obj = json.loads(response[1].decode("utf-8"), object_pairs_hook=_unique_object, parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
        except (ValueError, UnicodeError, RecursionError):
            failed = True
        if failed:
            _fail()
        return obj

    def repository(self, owner, repo, *, token=None):
        return _repository(self._get(self._base(owner, repo), token), owner, repo, token)

    def branch(self, owner, repo, branch, *, token=None):
        base = self._base(owner, repo)
        if not _valid_branch(branch):
            _fail()
        obj = self._get(base + "/branches/" + quote(branch, safe=""), token)
        if type(obj) is not dict or type(obj.get("name")) is not str or obj["name"] != branch:
            _fail()
        out = {}
        _add_text(out, "name", obj, "name", token)
        if type(obj.get("protected")) is bool:
            out["protected"] = obj["protected"]
        if type(obj.get("commit")) is dict:
            out["commit"] = _commit(obj["commit"], token)
        return out

    def commits(self, owner, repo, branch, *, per_page=20, page=1, token=None):
        base = self._base(owner, repo)
        if (not _valid_branch(branch) or type(per_page) is not int or not 1 <= per_page <= 30
                or type(page) is not int or not 1 <= page <= 10):
            _fail()
        query = urlencode({"sha": branch, "per_page": per_page, "page": page})
        obj = self._get(base + "/commits?" + query, token)
        if type(obj) is not list or len(obj) > per_page:
            _fail()
        return [_commit(item, token) for item in obj]
