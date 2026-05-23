import hmac
import time
from hashlib import sha256

from fastapi import Depends, Form, HTTPException, Request, status

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
    if not hmac.compare_digest(parts[2], _signature(payload)):
        return False
    try:
        created_at = int(parts[1])
    except ValueError:
        return False
    return time.time() - created_at <= get_settings().admin_session_ttl_seconds


def csrf_token_for_session(value: str | None) -> str:
    if not value or not verify_session_cookie(value):
        return ""
    return hmac.new(get_settings().secret_key.encode(), f"csrf:{value}".encode(), sha256).hexdigest()


def verify_csrf_token(session_cookie: str | None, csrf_token: str | None) -> bool:
    expected = csrf_token_for_session(session_cookie)
    return bool(expected and csrf_token and hmac.compare_digest(expected, csrf_token))


def require_admin(request: Request) -> None:
    if not verify_session_cookie(request.cookies.get(COOKIE_NAME)):
        raise HTTPException(status_code=status.HTTP_303_SEE_OTHER, headers={"Location": "/admin/login"})


def admin_dependency(_: None = Depends(require_admin)) -> None:
    return None


def csrf_dependency(request: Request, csrf_token: str = Form(...)) -> None:
    require_admin(request)
    if not verify_csrf_token(request.cookies.get(COOKIE_NAME), csrf_token):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid CSRF token")
