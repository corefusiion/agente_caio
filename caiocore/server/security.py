"""ASGI boundary for the gateway. Environment changes apply per request.

CAIO_API_TOKEN: require this Bearer token on every non-webhook request.
CAIO_ALLOWED_HOSTS: comma-separated additional exact hostnames (no wildcards).
CAIO_ALLOWED_ORIGINS: comma-separated exact browser origins (for trusted proxies/dev).
CAIO_EVOLUTION_WEBHOOK_TOKEN: independent X-Evolution-Webhook-Token secret.
A reverse proxy must preserve Host/Origin, enforce its own auth, and not be
reachable in a way that makes untrusted callers appear to be local clients.
"""
import hmac
import ipaddress
import os
from urllib.parse import urlsplit

from starlette.responses import JSONResponse


def _secret_matches(expected, supplied):
    # compare_digest on bytes also handles arbitrary UTF-8 input safely.
    return bool(expected and supplied) and hmac.compare_digest(
        expected.encode("utf-8"), supplied.encode("utf-8")
    )


def _hostname(authority):
    try:
        if not authority or any(c in authority for c in " /\\@?#,\t\r\n"):
            return None
        url = urlsplit("http://" + authority)
        host = url.hostname
        _ = url.port  # validate malformed and out-of-range ports
        if not host or url.username or url.password or url.path or url.query or url.fragment:
            return None
        if host.endswith("."):
            return None
        return host.lower()
    except ValueError:
        return None


def _origin(value):
    try:
        u = urlsplit(value)
        if u.scheme not in ("http", "https") or not _hostname(u.netloc):
            return None
        if u.path or u.query or u.fragment:
            return None
        port = u.port or (443 if u.scheme == "https" else 80)
        return (u.scheme, u.hostname.lower(), port)
    except ValueError:
        return None


def _loopback(host):
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


class SecurityMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] not in ("http", "websocket"):
            return await self.app(scope, receive, send)
        headers = {}
        for name, value in scope.get("headers", []):
            name = name.lower()
            if name in (b"host", b"origin", b"authorization", b"x-evolution-webhook-token") and name in headers:
                return await self._deny(scope, receive, send, 400)
            headers[name] = value.decode("latin-1")
        host = _hostname(headers.get(b"host", ""))
        allowed_hosts = {"localhost", "127.0.0.1", "::1"}
        allowed_hosts.update(x.strip().lower() for x in os.getenv("CAIO_ALLOWED_HOSTS", "").split(",") if x.strip())
        if not host or host not in allowed_hosts:
            return await self._deny(scope, receive, send, 403)

        origin = headers.get(b"origin")
        if origin is not None:
            parsed = _origin(origin)
            scheme = scope.get("scheme", "http")
            scheme = {"ws": "http", "wss": "https"}.get(scheme, scheme)
            target = _origin(f"{scheme}://{headers[b'host']}")
            permitted = {_origin(x.strip()) for x in os.getenv("CAIO_ALLOWED_ORIGINS", "").split(",") if x.strip()}
            permitted.discard(None)
            if not parsed or (parsed != target and parsed not in permitted):
                return await self._deny(scope, receive, send, 403)
        elif scope["type"] == "websocket":
            return await self._deny(scope, receive, send, 403)

        is_webhook = (scope["type"] == "http" and scope.get("method") == "POST"
                      and scope.get("path") == "/api/webhooks/evolution")
        if is_webhook:
            if not _secret_matches(os.getenv("CAIO_EVOLUTION_WEBHOOK_TOKEN", ""),
                                   headers.get(b"x-evolution-webhook-token", "")):
                return await self._deny(scope, receive, send, 403)
        else:
            token = os.getenv("CAIO_API_TOKEN", "")
            authorization = headers.get(b"authorization", "")
            authenticated = authorization.startswith("Bearer ") and _secret_matches(token, authorization[7:])
            client = scope.get("client")
            peer = client[0] if client else ""
            # Local-only mode requires a loopback Host and socket peer. Reject
            # forwarding metadata: it must never be trusted to grant access.
            forwarded = any(h in headers for h in (b"forwarded", b"x-forwarded-for", b"x-forwarded-host", b"x-forwarded-proto", b"x-forwarded-port", b"x-real-ip"))
            local = host in {"localhost", "127.0.0.1", "::1"} and _loopback(peer) and not forwarded
            if not authenticated and (token or not local):
                return await self._deny(scope, receive, send, 401)
            # Browser writes must carry an allowed Origin; token-authenticated
            # non-browser clients may omit it. Webhook has its own credential.
            if (scope["type"] == "http" and scope.get("method") not in {"GET", "HEAD", "OPTIONS"}
                    and not origin and not authenticated):
                return await self._deny(scope, receive, send, 403)
        await self.app(scope, receive, send)

    @staticmethod
    async def _deny(scope, receive, send, status):
        if scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": 1008})
        else:
            await JSONResponse({"detail": "Access denied"}, status_code=status)(scope, receive, send)
