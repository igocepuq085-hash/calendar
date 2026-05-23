from pathlib import Path

import pytest
from fastapi import HTTPException

from app.auth import csrf_token_for_session, make_session_cookie, verify_csrf_token, verify_session_cookie
from app.routers.admin import _safe_admin_return_to, _upload_target


def test_admin_return_to_rejects_external_urls() -> None:
    assert _safe_admin_return_to("https://evil.example/phish", "/admin/kip") == "/admin/kip"
    assert _safe_admin_return_to("//evil.example/phish", "/admin/kip") == "/admin/kip"
    assert _safe_admin_return_to("/admin/employees/1", "/admin/kip") == "/admin/employees/1"


def test_upload_target_accepts_only_xlsx_inside_upload_dir() -> None:
    upload_dir = Path("uploads").resolve()
    target, original_name = _upload_target(upload_dir, r"..\..\schedule.xlsx")

    assert original_name == "schedule.xlsx"
    assert target.suffix == ".xlsx"
    assert upload_dir in target.parents

    with pytest.raises(HTTPException):
        _upload_target(upload_dir, "payload.exe")


def test_admin_session_and_csrf_token_are_bound_together() -> None:
    session = make_session_cookie("admin")
    csrf = csrf_token_for_session(session)

    assert verify_session_cookie(session)
    assert verify_csrf_token(session, csrf)
    assert not verify_csrf_token(session, "bad-token")
