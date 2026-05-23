from datetime import date, datetime, time

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.auth import COOKIE_NAME
from app.config import get_settings
from app.database import Base
from app.main import app
from app.models import Employee, EventType, KipStatus, KnowledgeCheck, MedicalCheck, NotificationSetting, ShiftType, WorkShift
from app.routers.admin import LOGIN_ATTEMPTS, MAX_LOGIN_ATTEMPTS, update_medical_check
from app.routers.calendar import ICS_HEADERS
from app.database import get_db
from app.services.calendar_notices import create_calendar_notice
from app.services.ics import build_admin_calendar, build_employee_calendar
from app.services.kip import KIP_LATE_ERROR, change_kip_date, default_shift_times, plan_kip_record, recalculate_all_kip_records


@pytest.fixture()
def db() -> Session:
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


def employee(db: Session) -> Employee:
    item = Employee(full_name="Иванов И.И.", tab_number="1")
    db.add(item)
    db.flush()
    return item


def add_shift(db: Session, employee_id: int, shift_date: date, shift_type: ShiftType = ShiftType.day) -> WorkShift:
    start, end = default_shift_times(shift_date, shift_type)
    shift = WorkShift(
        employee_id=employee_id,
        shift_date=shift_date,
        shift_type=shift_type,
        start_datetime=start,
        end_datetime=end,
        raw_value="11.5" if shift_type == ShiftType.day else "4",
    )
    db.add(shift)
    db.flush()
    return shift


def test_admin_login_redirects_and_sets_session_cookie() -> None:
    settings = get_settings()
    LOGIN_ATTEMPTS.clear()

    response = TestClient(app).post(
        "/admin/login",
        data={"username": settings.admin_username, "password": settings.admin_password},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/admin"
    assert COOKIE_NAME in response.headers["set-cookie"]


def test_admin_login_rate_limit_returns_login_page_redirect_for_wrong_password() -> None:
    settings = get_settings()
    LOGIN_ATTEMPTS.clear()
    client = TestClient(app)

    for _ in range(MAX_LOGIN_ATTEMPTS):
        client.post(
            "/admin/login",
            data={"username": settings.admin_username, "password": "wrong"},
            follow_redirects=False,
        )

    response = client.post(
        "/admin/login",
        data={"username": settings.admin_username, "password": "still-wrong"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/admin/login?locked=1"
    LOGIN_ATTEMPTS.clear()


def test_admin_login_valid_credentials_clear_previous_failed_attempts() -> None:
    settings = get_settings()
    LOGIN_ATTEMPTS.clear()
    client = TestClient(app)

    for _ in range(MAX_LOGIN_ATTEMPTS):
        client.post(
            "/admin/login",
            data={"username": settings.admin_username, "password": "wrong"},
            follow_redirects=False,
        )

    response = client.post(
        "/admin/login",
        data={"username": settings.admin_username, "password": settings.admin_password},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/admin"
    assert COOKIE_NAME in response.headers["set-cookie"]
    assert LOGIN_ATTEMPTS == {}


def test_kip_planned_on_due_date_when_shift_exists(db: Session) -> None:
    worker = employee(db)
    add_shift(db, worker.id, date(2026, 5, 10))

    record = plan_kip_record(db, employee_id=worker.id, last_kip_date=date(2026, 1, 10), today=date(2026, 1, 11))

    assert record.due_date == date(2026, 5, 10)
    assert record.planned_date == date(2026, 5, 10)
    assert record.status == KipStatus.planned


def test_kip_searches_back_when_due_date_is_day_off(db: Session) -> None:
    worker = employee(db)
    add_shift(db, worker.id, date(2026, 5, 8))
    add_shift(db, worker.id, date(2026, 5, 10), ShiftType.off)

    record = plan_kip_record(db, employee_id=worker.id, last_kip_date=date(2026, 1, 10), today=date(2026, 1, 11))

    assert record.due_date == date(2026, 5, 10)
    assert record.planned_date == date(2026, 5, 8)


def test_kip_is_not_planned_forward_after_due_date(db: Session) -> None:
    worker = employee(db)
    add_shift(db, worker.id, date(2026, 5, 11))

    record = plan_kip_record(db, employee_id=worker.id, last_kip_date=date(2026, 1, 10), today=date(2026, 1, 11))

    assert record.due_date == date(2026, 5, 10)
    assert record.planned_date is None
    assert record.status == KipStatus.conflict


def test_manual_kip_date_after_due_date_is_forbidden(db: Session) -> None:
    worker = employee(db)
    add_shift(db, worker.id, date(2026, 5, 10))
    add_shift(db, worker.id, date(2026, 5, 11))
    record = plan_kip_record(db, employee_id=worker.id, last_kip_date=date(2026, 1, 10), today=date(2026, 1, 11))

    with pytest.raises(ValueError, match=KIP_LATE_ERROR):
        change_kip_date(db, record, datetime(2026, 5, 11, 9, 0))


def test_manual_kip_date_outside_shift_is_forbidden(db: Session) -> None:
    worker = employee(db)
    add_shift(db, worker.id, date(2026, 5, 10))
    record = plan_kip_record(db, employee_id=worker.id, last_kip_date=date(2026, 1, 10), today=date(2026, 1, 11))

    with pytest.raises(ValueError, match="рабочее время"):
        change_kip_date(db, record, datetime(2026, 5, 10, 23, 0))


def test_calendar_returns_one_common_ics_with_alarms(db: Session) -> None:
    worker = employee(db)
    add_shift(db, worker.id, date(2026, 5, 10))
    plan_kip_record(db, employee_id=worker.id, last_kip_date=date(2026, 1, 10), today=date(2026, 1, 11))
    db.add(NotificationSetting(event_type=EventType.kip, amount=1, unit="days", enabled=True))
    db.flush()

    content = build_employee_calendar(db, worker).decode("utf-8")

    assert "BEGIN:VCALENDAR" in content
    assert "Дневная" in content
    assert "КИП" in content
    assert "BEGIN:VALARM" in content
    assert "TRIGGER:-P7D" in content
    assert "TRIGGER:-P1D" in content
    assert content.count("BEGIN:VCALENDAR") == 1
    assert "LAST-MODIFIED" in content
    assert "SEQUENCE" in content


def test_shift_uid_is_stable_after_schedule_reimport(db: Session) -> None:
    worker = employee(db)
    shift = add_shift(db, worker.id, date(2026, 5, 10))
    first_content = build_employee_calendar(db, worker).decode("utf-8")
    stable_uid = f"shift-{worker.id}-20260510-day"

    db.delete(shift)
    db.flush()
    add_shift(db, worker.id, date(2026, 5, 10))
    second_content = build_employee_calendar(db, worker).decode("utf-8")

    assert stable_uid in first_content
    assert stable_uid in second_content
    assert first_content.split(stable_uid, 1)[1].split("@kip-calendar-service", 1)[0] == second_content.split(stable_uid, 1)[1].split("@kip-calendar-service", 1)[0]


def test_work_shift_calendar_uses_yekaterinburg_marker_times(db: Session) -> None:
    worker = employee(db)
    add_shift(db, worker.id, date(2026, 5, 22), ShiftType.day)
    night_shift = add_shift(db, worker.id, date(2026, 5, 23), ShiftType.night)
    night_shift.end_datetime = datetime(2026, 5, 25, 3, 30)
    night_shift.raw_value = "55.5"

    content = build_employee_calendar(db, worker).decode("utf-8")

    assert "X-WR-TIMEZONE:Asia/Yekaterinburg" in content
    assert "DTSTART;TZID=Asia/Yekaterinburg:20260522T080000" in content
    assert "DTSTART;TZID=Asia/Yekaterinburg:20260523T200000" in content
    assert "DTEND;TZID=Asia/Yekaterinburg" not in content
    assert "20260523T010000" not in content
    assert "20260525T033000" not in content


def test_calendar_notice_appears_in_employee_calendar_with_alarm(db: Session) -> None:
    worker = employee(db)
    create_calendar_notice(
        db,
        employee_id=worker.id,
        title="⚠️ Изменение графика",
        description="2026-05-22: day -> night",
        now=datetime(2026, 5, 22, 10, 0),
    )
    db.flush()

    content = build_employee_calendar(db, worker).decode("utf-8")

    assert "SUMMARY:⚠️ Изменение графика" in content
    assert "DESCRIPTION:2026-05-22: day -> night" in content
    assert "DTSTART;TZID=Asia/Yekaterinburg:20260522T105500" in content
    assert "TRIGGER:PT0M" in content


def test_calendar_notice_does_not_appear_in_admin_calendar(db: Session) -> None:
    worker = employee(db)
    create_calendar_notice(
        db,
        employee_id=worker.id,
        title="⚠️ Изменение графика",
        description="2026-05-22: day -> night",
        now=datetime(2026, 5, 22, 10, 0),
    )
    db.flush()

    content = build_admin_calendar(db).decode("utf-8")

    assert "Изменение графика" not in content


def test_manual_medical_date_update_changes_calendar_and_redirects_saved(db: Session) -> None:
    worker = employee(db)
    check = MedicalCheck(employee_id=worker.id, previous_date=date(2026, 1, 1), next_date=date(2026, 7, 1))
    db.add(check)
    db.flush()

    response = update_medical_check(
        check.id,
        previous_date="2026-01-01",
        next_date="2026-08-01",
        return_to=f"/admin/employees/{worker.id}",
        db=db,
    )

    content = build_employee_calendar(db, worker).decode("utf-8")

    assert response.headers["location"].endswith("?saved=%D0%A1%D0%BE%D1%85%D1%80%D0%B0%D0%BD%D0%B5%D0%BD%D0%BE")
    assert "DTSTART;VALUE=DATE:20260801" in content
    assert "DTSTART;VALUE=DATE:20260701" not in content
    assert "SUMMARY:⚠️ Изменение медкомиссии" in content


def test_calendar_endpoint_disables_cache(db: Session) -> None:
    worker = employee(db)
    worker.calendar_token = "test-token"
    db.flush()

    def override_db():
        yield db

    app.dependency_overrides[get_db] = override_db
    try:
        response = TestClient(app).get("/cal/test-token.ics")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    for header, value in ICS_HEADERS.items():
        assert response.headers[header] == value


def test_kip_recalculates_after_work_schedule_changes(db: Session) -> None:
    worker = employee(db)
    add_shift(db, worker.id, date(2026, 5, 10))
    record = plan_kip_record(db, employee_id=worker.id, last_kip_date=date(2026, 1, 10), today=date(2026, 1, 11))
    assert record.planned_date == date(2026, 5, 10)

    for shift in list(worker.work_shifts):
        db.delete(shift)
    db.flush()
    add_shift(db, worker.id, date(2026, 5, 9))
    recalculate_all_kip_records(db, today=date(2026, 1, 11))

    assert record.planned_date == date(2026, 5, 9)


def test_admin_calendar_contains_access_events_without_work_shifts(db: Session) -> None:
    worker = employee(db)
    add_shift(db, worker.id, date(2026, 5, 10))
    plan_kip_record(db, employee_id=worker.id, last_kip_date=date(2026, 1, 10), today=date(2026, 1, 11))
    db.add(KnowledgeCheck(employee_id=worker.id, check_type="Проверка знаний по ОТ", previous_date=date(2026, 1, 1), next_date=date(2026, 6, 1)))
    db.add(MedicalCheck(employee_id=worker.id, previous_date=date(2026, 1, 1), next_date=date(2026, 7, 1)))
    db.flush()

    content = build_admin_calendar(db).decode("utf-8")

    assert "КИП" in content
    assert "Проверка" in content
    assert "Медицинская" in content
    assert "Дневная" not in content
    assert "TRIGGER:-P14D" in content
    assert "TRIGGER:-P30D" in content
