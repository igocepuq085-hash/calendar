from datetime import date, datetime, time

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.database import Base
from app.main import app
from app.models import Employee, EventType, KipStatus, KnowledgeCheck, MedicalCheck, NotificationSetting, ShiftType, WorkShift
from app.routers.calendar import ICS_HEADERS
from app.database import get_db
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
