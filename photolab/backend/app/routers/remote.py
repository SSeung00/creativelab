"""Remote (phone) access: PIN lock endpoints and Tailscale status."""

import io
import json
import shutil
import subprocess
import sys
from pathlib import Path

import segno
from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field

from app import auth
from app.config import PORT
from app.db import SessionDep

router = APIRouter(tags=["remote"])

# RunLab owns 443 and MoneyLab 8443 on https://<pc>.ts.net; PhotoLab takes 10000.
HTTPS_PORT = 10000
SERVE_CMD = f"tailscale serve --bg --https={HTTPS_PORT} {PORT}"


@router.get("/auth/status")
def auth_status(request: Request, session: SessionDep) -> dict:
    local = auth.is_local(request.scope)
    return {
        "local": local,
        "pin_set": auth.pin_is_set(session),
        "authed": local or auth.token_valid(session, request.cookies.get(auth.COOKIE)),
        "retry_after": auth.retry_after(),
    }


class UnlockBody(BaseModel):
    pin: str = Field(min_length=1, max_length=32)


@router.post("/auth/unlock")
def unlock(body: UnlockBody, request: Request, response: Response, session: SessionDep) -> dict:
    if not auth.pin_is_set(session):
        raise HTTPException(403, "PC의 설정 → 휴대폰에서 보기에서 PIN을 먼저 정해 주세요.")
    wait = auth.retry_after()
    if wait:
        raise HTTPException(429, f"너무 많이 틀렸어요. {wait}초 뒤에 다시 시도해 주세요.")
    if not auth.check_pin(session, body.pin):
        auth.record_failure()
        left = auth.attempts_left()
        raise HTTPException(401, "PIN이 맞지 않아요." + (f" ({left}번 남음)" if left > 0 else ""))
    auth.reset_failures()
    response.set_cookie(
        auth.COOKIE,
        auth.issue_token(session),
        max_age=auth.SESSION_DAYS * 86400,
        httponly=True,
        samesite="lax",
        secure=auth.is_https(request.scope),
    )
    return {"ok": True}


@router.post("/auth/lock")
def lock(response: Response) -> dict:
    response.delete_cookie(auth.COOKIE)
    return {"ok": True}


class PinBody(BaseModel):
    pin: str | None = Field(default=None, pattern=r"^\d{4,8}$")


@router.put("/auth/pin")
def put_pin(body: PinBody, request: Request, session: SessionDep) -> dict:
    """Set, change or (pin=null) remove the PIN. Only from this PC."""
    if not auth.is_local(request.scope):
        raise HTTPException(403, "PIN은 PC에서만 바꿀 수 있어요.")
    auth.set_pin(session, body.pin)
    auth.reset_failures()
    return {"pin_set": body.pin is not None}


# ---------- Tailscale ----------

def _tailscale_exe() -> str | None:
    found = shutil.which("tailscale")
    if found:
        return found
    default = Path(r"C:\Program Files\Tailscale\tailscale.exe")
    return str(default) if default.exists() else None


def _run(exe: str, *args: str) -> dict | None:
    flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    try:
        out = subprocess.run(
            [exe, *args, "--json"], capture_output=True, text=True, timeout=6,
            # Tailscale prints UTF-8 JSON; the Windows default (cp949) would fail on device names.
            encoding="utf-8", errors="replace", creationflags=flags, check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    try:
        return json.loads(out.stdout) if (out.stdout or "").strip() else {}
    except json.JSONDecodeError:
        return None


def _qr_svg(text: str) -> str:
    buf = io.BytesIO()
    segno.make(text, error="m").save(buf, kind="svg", scale=6, border=2, xmldecl=False, dark="#000", light="#fff")
    return buf.getvalue().decode()


@router.get("/remote/status")
def remote_status(session: SessionDep) -> dict:
    """What the phone-access checklist needs: installed → logged in → serve → URL."""
    exe = _tailscale_exe()
    res: dict = {
        "installed": exe is not None,
        "running": False,
        "dns_name": None,
        "url": None,
        "serving": False,
        "serve_cmd": SERVE_CMD,
        "pin_set": auth.pin_is_set(session),
        "qr_svg": None,
    }
    if not exe:
        return res
    st = _run(exe, "status")
    if not st:
        return res
    res["running"] = st.get("BackendState") == "Running"
    dns = ((st.get("Self") or {}).get("DNSName") or "").rstrip(".")
    if dns:
        res["dns_name"] = dns
        res["url"] = f"https://{dns}:{HTTPS_PORT}"
        res["qr_svg"] = _qr_svg(res["url"])
    serve = _run(exe, "serve", "status") or {}
    for site in (serve.get("Web") or {}).values():
        for handler in (site.get("Handlers") or {}).values():
            if f":{PORT}" in str(handler.get("Proxy", "")):
                res["serving"] = True
    return res
