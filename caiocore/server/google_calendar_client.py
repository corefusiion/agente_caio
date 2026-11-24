"""Isolated Google Calendar read-only client; no routes or automatic connection.

The caller must protect all returned credentials and event data, and must bind
sessions to an authenticated operator before enabling an OAuth callback. The
injected transport is a trusted testing seam, not an endpoint supplied by users.
"""
from __future__ import annotations

import http.client
import json
import re
import ssl
from dataclasses import dataclass, field
from urllib.parse import urlencode

from .google_oauth_local import (
    OAuthError, PendingAuthorizations, REDIRECT_URI, SCOPES,
    accept_token_response,
)

TOKEN_URL = 'https://oauth2.googleapis.com/token'
REVOKE_URL = 'https://oauth2.googleapis.com/revoke'
EVENTS_URL = 'https://www.googleapis.com/calendar/v3/calendars/primary/events'
CALENDAR_SCOPE = SCOPES['calendar_read']
TIMEOUT_SECONDS = 10
MAX_RESPONSE_BYTES = 1024 * 1024
_MAX_SECRET = 8192
_BEARER = re.compile(r'[A-Za-z0-9\-._~+/]+=*\Z')
_VERIFIER = re.compile(r'[A-Za-z0-9\-._~]{43,128}\Z')


@dataclass(frozen=True)
class TransportResponse:
    status: int
    body: bytes = field(repr=False)


@dataclass(frozen=True)
class TokenResult:
    # Secrets must never appear in repr/logs; consumers must not log fields.
    access_token: str = field(repr=False)
    record: dict = field(repr=False)
    expires_in: int | None = None


def _secret(value, *, bearer=False):
    if (not isinstance(value, str) or not value or len(value) > _MAX_SECRET
            or not value.isascii() or any(ord(c) < 33 or ord(c) > 126 for c in value)
            or (bearer and not _BEARER.fullmatch(value))):
        raise OAuthError('Invalid credential')
    return value


def _record(record):
    if (not isinstance(record, dict) or set(record) != {'refresh_token', 'scopes'}
            or record['scopes'] != [CALENDAR_SCOPE]):
        raise OAuthError('Invalid calendar credential')
    _secret(record['refresh_token'])


def _form(values):
    return urlencode(values).encode('ascii')


def https_transport(method, url, headers, body):
    """Trusted default transport: explicit allowlist, verified TLS, no redirects.

    Timeout is a socket-operation timeout, not a total wall-clock deadline.
    Response bodies are capped before they leave this function.
    """
    paths = {
        ('POST', TOKEN_URL): ('oauth2.googleapis.com', '/token'),
        ('POST', REVOKE_URL): ('oauth2.googleapis.com', '/revoke'),
        ('GET', EVENTS_URL): ('www.googleapis.com', '/calendar/v3/calendars/primary/events'),
    }
    # GET query is produced internally; the path and host must stay fixed.
    base, separator, query = url.partition('?')
    key = (method, base)
    if key not in paths or (separator and method != 'GET') or '#' in url:
        raise OAuthError('Invalid Google endpoint')
    host, path = paths[key]
    if separator:
        path += '?' + query
    conn = None
    try:
        conn = http.client.HTTPSConnection(host, timeout=TIMEOUT_SECONDS,
                                            context=ssl.create_default_context())
        conn.set_debuglevel(0)
        conn.request(method, path, body=body, headers=headers)
        response = conn.getresponse()
        data = response.read(MAX_RESPONSE_BYTES + 1)
        if len(data) > MAX_RESPONSE_BYTES:
            raise OAuthError('Google response too large')
        return TransportResponse(response.status, data)
    except OAuthError:
        raise
    except Exception:
        raise OAuthError('Google request failed') from None
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass  # Cleanup must not leak an exception containing secrets.


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('Duplicate JSON field')
        result[key] = value
    return result


def _reject_constant(_value):
    raise ValueError('Non-standard JSON')


class GoogleCalendarClient:
    def __init__(self, client_id: str, *, client_secret: str | None = None,
                 transport=https_transport):
        # PendingAuthorizations performs its own client ID checks too.
        _secret(client_id)
        if len(client_id) > 512:
            raise OAuthError('Invalid client ID')
        if client_secret is not None:
            _secret(client_secret)
        if not callable(transport):
            raise OAuthError('Invalid transport')
        self._client_id = client_id
        self._client_secret = client_secret
        self._pending = PendingAuthorizations(client_id)
        self._transport = transport

    def start(self, session: bytes) -> str:
        return self._pending.start(session, ['calendar_read'])

    def cancel_all(self):
        self._pending.cancel_all()

    def cancel(self, session: bytes, state: str):
        # Consume a denied authorization without attempting a token exchange.
        self._pending.consume(session, state)

    def _request(self, method, url, *, values=None, access_token=None):
        headers = {'Accept': 'application/json'}
        body = None
        if values is not None:
            headers['Content-Type'] = 'application/x-www-form-urlencoded'
            body = _form(values)
        if access_token is not None:
            headers['Authorization'] = 'Bearer ' + _secret(access_token, bearer=True)
        try:
            response = self._transport(method, url, headers, body)
        except Exception:
            raise OAuthError('Google request failed') from None
        if (not isinstance(response, TransportResponse) or type(response.status) is not int
                or not isinstance(response.body, bytes)):
            raise OAuthError('Invalid Google response')
        if len(response.body) > MAX_RESPONSE_BYTES:
            raise OAuthError('Google response too large')
        # Includes all redirects: do not follow or report the Location header.
        if not 200 <= response.status < 300:
            raise OAuthError('Google request rejected')
        return response.body

    def _json(self, *args, **kwargs):
        raw = self._request(*args, **kwargs)
        try:
            value = json.loads(raw, object_pairs_hook=_unique_object,
                               parse_constant=_reject_constant)
        except (ValueError, UnicodeDecodeError, RecursionError):
            raise OAuthError('Invalid Google response') from None
        if not isinstance(value, dict):
            raise OAuthError('Invalid Google response')
        return value

    def _token(self, response, *, previous=None):
        # Google may omit scope and refresh_token when refreshing. The retained
        # scope and token must have come from a previously validated record.
        checked = dict(response)
        if previous is not None:
            checked.setdefault('refresh_token', previous['refresh_token'])
            checked.setdefault('scope', CALENDAR_SCOPE)
        record = accept_token_response(checked, (CALENDAR_SCOPE,))
        _record(record)
        access = _secret(checked.get('access_token'), bearer=True)
        expiry = checked.get('expires_in')
        if expiry is not None and (type(expiry) is not int or not 0 < expiry <= 31536000):
            raise OAuthError('Invalid token response')
        return TokenResult(access, record, expiry)

    def _client_parameters(self):
        result = {'client_id': self._client_id}
        if self._client_secret is not None:
            result['client_secret'] = self._client_secret
        return result

    def exchange(self, session: bytes, state: str, code: str) -> TokenResult:
        _secret(code)
        # Consume before any network request; a failed exchange cannot be retried.
        verifier, scopes = self._pending.consume(session, state)
        if scopes != (CALENDAR_SCOPE,) or not _VERIFIER.fullmatch(verifier):
            raise OAuthError('Invalid authorization')
        response = self._json('POST', TOKEN_URL, values={
            **self._client_parameters(), 'code': code, 'code_verifier': verifier,
            'grant_type': 'authorization_code', 'redirect_uri': REDIRECT_URI,
        })
        return self._token(response)

    def refresh(self, record: dict) -> TokenResult:
        _record(record)
        response = self._json('POST', TOKEN_URL, values={
            **self._client_parameters(), 'refresh_token': record['refresh_token'],
            'grant_type': 'refresh_token',
        })
        return self._token(response, previous=record)

    def revoke(self, record: dict) -> None:
        _record(record)
        # An HTTP success indicates Google accepted the request; local deletion
        # remains a separate operation. A failure must never imply revocation.
        self._request('POST', REVOKE_URL, values={'token': record['refresh_token']})

    def list_events(self, access_token: str, *, page_token: str | None = None,
                    max_results: int = 250) -> dict:
        if type(max_results) is not int or not 1 <= max_results <= 2500:
            raise OAuthError('Invalid event limit')
        query = {'maxResults': str(max_results)}
        if page_token is not None:
            query['pageToken'] = _secret(page_token)
        response = self._json('GET', EVENTS_URL + '?' + urlencode(query),
                              access_token=access_token)
        if not isinstance(response.get('items'), list):
            raise OAuthError('Invalid calendar response')
        if 'nextPageToken' in response:
            _secret(response['nextPageToken'])
        return response
