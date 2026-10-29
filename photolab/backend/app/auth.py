"""PIN lock for remote access (phone via Tailscale).

Requests from this PC (loopback, localhost / *.localhost Host header, no proxy headers) are trusted.
Anything else — notably `tailscale serve`, which proxies from loopback but adds forwarding
headers and keeps the *.ts.net Host — must present a session cookie issued after the PIN.
Remote API access stays closed until a PIN has been set on the PC.
"""

import hashlib
import hmac
import json
import secrets
import time
from collections import deque
from datetime import datetime

from sqlmodel import Session

from app.db import engine
from app.models import Setting

COOKIE = "photolab_auth"
SESSION_DAYS = 180
PIN_KEY = "auth_pin"
SECRET_KEY = "auth_secret"

LOCAL_CLIENTS = {"127.0.0.1", "::1"}
LOCAL_HOSTS = {"localhost", "127.0.0.1", "[::1]", "::1"}
PROXY_HEADERS = (b"x-forwarded-for", b"forwarded", b"x-real-ip", b"tailscale-user-login")

# API paths a locked remote client may call (to learn it's locked and to unlock).

OPEN_PATHS = {"/api/auth/status", "/api/auth/unlock", "/api/health"}

MAX_FAILS = 5
FAIL_WINDOW_S = 300
_fails: deque[float] = deque()


# ---------- request classification ----------

def _host_name(host: str) -> str:
    if host.startswith("["):  # [::1]:8000
        return host.split("]")[0] + "]"
    return host.rsplit(":", 1)[0] if host.count(":") == 1 else host


def is_local(scope: dict) -> bool:
    client = scope.get("client")
    if not client or client[0] not in LOCAL_CLIENTS:
        return False
    headers = dict(scope.get("headers") or [])
    if any(h in headers for h in PROXY_HEADERS):
        return False
    host = headers.get(b"host", b"").decode("latin-1").lower()
    name = _host_name(host)
    # <app>.localhost gives each app window its own taskbar entry; it is loopback by definition (RFC 6761).
    return name in LOCAL_HOSTS or name.endswith(".localhost")


def is_https(scope: dict) -> bool:
    headers = dict(scope.get("headers") or [])
    return headers.get(b"x-forwarded-proto", b"").decode() == "https" or scope.get("scheme") == "https"


# ---------- storage ----------

def _get(session: Session, key: str) -> str | None:
    row = session.get(Setting, key)
    return row.value if row else None


def _put(session: Session, key: str, value: str | None) -> None:
    row = session.get(Setting, key) or Setting(key=key)
    row.value = value
    row.updated_at = datetime.now()
    session.add(row)


def _hash(pin: str, salt: bytes) -> bytes:
    return hashlib.scrypt(pin.encode(), salt=salt, n=2**14, r=8, p=1, dklen=32)


def pin_is_set(session: Session) -> bool:
    return bool(_get(session, PIN_KEY))


def set_pin(session: Session, pin: str | None) -> None:
    """Set, change (pin) or remove (None) the PIN. Rotating the secret signs everyone out."""
    if pin is None:
        _put(session, PIN_KEY, None)
    else:
        salt = secrets.token_bytes(16)
        _put(session, PIN_KEY, json.dumps({"salt": salt.hex(), "hash": _hash(pin, salt).hex()}))
    _put(session, SECRET_KEY, secrets.token_hex(32))
    session.commit()


def check_pin(session: Session, pin: str) -> bool:
    raw = _get(session, PIN_KEY)
    if not raw:
        return False
    data = json.loads(raw)
    return hmac.compare_digest(_hash(pin, bytes.fromhex(data["salt"])).hex(), data["hash"])


# ---------- brute-force guard ----------

def retry_after() -> int:
    """Seconds until another attempt is allowed (0 = now)."""
    now = time.monotonic()
    while _fails and now - _fails[0] > FAIL_WINDOW_S:
        _fails.popleft()
    if len(_fails) < MAX_FAILS:
        return 0
    return int(FAIL_WINDOW_S - (now - _fails[0])) + 1


def attempts_left() -> int:
    return max(0, MAX_FAILS - len(_fails))


def record_failure() -> None:
    _fails.append(time.monotonic())


def reset_failures() -> None:
    _fails.clear()


# ---------- session tokens ----------

def _secret(session: Session) -> bytes | None:
    s = _get(session, SECRET_KEY)
    return bytes.fromhex(s) if s else None


def issue_token(session: Session) -> str:
    secret = _secret(session)
    assert secret is not None, "PIN must be set before issuing tokens"
    iat = str(int(time.time()))
    sig = hmac.new(secret, iat.encode(), hashlib.sha256).hexdigest()
    return f"{iat}.{sig}"


def token_valid(session: Session, token: str | None) -> bool:
    if not token or "." not in token or not pin_is_set(session):
        return False
    secret = _secret(session)
    if secret is None:
        return False
    iat, sig = token.split(".", 1)
    expected = hmac.new(secret, iat.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(sig, expected):
        return False
    return iat.isdigit() and time.time() - int(iat) < SESSION_DAYS * 86400


def cookie_from_scope(scope: dict) -> str | None:
    headers = dict(scope.get("headers") or [])
    raw = headers.get(b"cookie", b"").decode("latin-1")
    for part in raw.split(";"):
        name, _, value = part.strip().partition("=")
        if name == COOKIE:
            return value
    return None


def remote_authed(scope: dict) -> bool:
    with Session(engine) as session:
        return token_valid(session, cookie_from_scope(scope))


# ---------- ASGI middleware ----------

class RemoteAuthMiddleware:
    """Guards /api/* for non-local clients. Static app files stay public (no data in them)."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        path: str = scope.get("path", "")
        if not path.startswith("/api/") or path in OPEN_PATHS or is_local(scope):
            return await self.app(scope, receive, send)
        with Session(engine) as session:
            if not pin_is_set(session):
                return await _deny(send, 403, "pin_not_set", "PC의 설정 → 휴대폰에서 보기에서 PIN을 먼저 정해 주세요.")
            if not token_valid(session, cookie_from_scope(scope)):
                return await _deny(send, 401, "locked", "잠겨 있어요. PIN을 입력해 주세요.")
        return await self.app(scope, receive, send)


async def _deny(send, status: int, code: str, detail: str) -> None:
    body = json.dumps({"detail": detail, "code": code}, ensure_ascii=False).encode()
    await send({
        "type": "http.response.start",
        "status": status,
        "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())],
    })
    await send({"type": "http.response.body", "body": body})
