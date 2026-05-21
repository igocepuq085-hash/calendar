import hmac
import time
from hashlib import sha256

from fastapi import Depends, HTTPException, Request, status

from app.config import get_settings

COOKIE_NAME = "kip_admin_session"


def _signature(payload: str) -> str:
    secret = get_settings().secret_key.encode()
    return hmac.new(secret, payload.encode(), sha256).hexdigest()


def make_session_cookie(username: str) -> str:
    payload = f"{username}:{int(time.time())}"
    return f"{payload}:{_signature(payload)}"


def verify_session_cookie(value: str | None) -> bool:
    if not value:
        return False
    parts = value.split(":")
    if len(parts) != 3:
        return False
    payload = ":".join(parts[:2])
    return hmac.compare_digest(parts[2], _signature(payload))


def require_admin(request: Request) -> None:
    if not verify_session_cookie(request.cookies.get(COOKIE_NAME)):
        raise HTTPException(status_code=status.HTTP_303_SEE_OTHER, headers={"Location": "/admin/login"})


def admin_dependency(_: None = Depends(require_admin)) -> None:
    return None

