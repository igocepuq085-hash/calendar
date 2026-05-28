from pathlib import Path

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from app.auth import csrf_token_for_session, make_session_cookie, verify_csrf_token, verify_session_cookie
from app.routers.admin import _calendar_base_url, _safe_admin_return_to, _upload_target


def test_admin_return_to_rejects_external_urls() -> None:
    assert _safe_admin_return_to("https://evil.example/phish", "/admin/knowledge") == "/admin/knowledge"
    assert _safe_admin_return_to("//evil.example/phish", "/admin/knowledge") == "/admin/knowledge"
    assert _safe_admin_return_to("/admin/employees/1", "/admin/knowledge") == "/admin/employees/1"


def test_upload_target_accepts_only_xlsx_inside_upload_dir() -> None:
    upload_dir = Path("uploads").resolve()
    target, original_name = _upload_target(upload_dir, r"..\..\schedule.xlsx")

    assert original_name == "schedule.xlsx"
    assert target.suffix == ".xlsx"
    assert upload_dir in target.parents

    with pytest.raises(HTTPException):
        _upload_target(upload_dir, "payload.exe")


def test_calendar_base_url_uses_forwarded_public_host_when_default_is_localhost() -> None:
    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/admin",
            "headers": [
                (b"host", b"calendar.example"),
                (b"x-forwarded-proto", b"https"),
            ],
        }
    )

    assert _calendar_base_url(request) == "https://calendar.example"


def test_admin_session_and_csrf_token_are_bound_together() -> None:
    session = make_session_cookie("admin")
    csrf = csrf_token_for_session(session)

    assert verify_session_cookie(session)
    assert verify_csrf_token(session, csrf)
    assert not verify_csrf_token(session, "bad-token")
