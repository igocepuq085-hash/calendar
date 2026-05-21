from datetime import date, datetime, time, timedelta

from dateutil.relativedelta import relativedelta
from sqlalchemy import Select, select
from sqlalchemy.orm import Session

from app.models import AuditLog, KipRecord, KipStatus, ShiftType, WorkShift

KIP_LATE_ERROR = "Нельзя назначить КИП позже крайнего срока. КИП является основанием допуска машиниста к работе."


def calculate_due_date(last_kip_date: date) -> date:
    return last_kip_date + relativedelta(months=4)


def working_shift_query(employee_id: int, target_date: date) -> Select[tuple[WorkShift]]:
    return (
        select(WorkShift)
        .where(
            WorkShift.employee_id == employee_id,
            WorkShift.shift_date == target_date,
            WorkShift.shift_type.in_([ShiftType.day, ShiftType.night, ShiftType.training]),
        )
        .order_by(WorkShift.start_datetime.is_(None), WorkShift.start_datetime)
    )


def find_working_shift_not_after_due(db: Session, employee_id: int, due_date: date, lookback_days: int = 30) -> WorkShift | None:
    for offset in range(0, lookback_days + 1):
        candidate = due_date - timedelta(days=offset)
        shift = db.scalar(working_shift_query(employee_id, candidate))
        if shift:
            return shift
    return None


def plan_kip_record(
    db: Session,
    *,
    employee_id: int,
    last_kip_date: date,
    today: date | None = None,
    source: str | None = None,
    raw_value: str | None = None,
) -> KipRecord:
    due_date = calculate_due_date(last_kip_date)
    record = KipRecord(
        employee_id=employee_id,
        last_kip_date=last_kip_date,
        due_date=due_date,
        status=KipStatus.planned,
        source=source,
        raw_value=raw_value,
    )
    db.add(record)
    db.flush()
    recalculate_kip_record(db, record, today=today)
    return record


def recalculate_kip_record(db: Session, record: KipRecord, today: date | None = None) -> KipRecord:
    if record.status == KipStatus.completed:
        return record
    today = today or date.today()
    record.due_date = calculate_due_date(record.last_kip_date)
    shift = find_working_shift_not_after_due(db, record.employee_id, record.due_date)
    record.planned_date = shift.shift_date if shift else None
    record.planned_start = shift.start_datetime if shift else None
    record.planned_end = shift.end_datetime if shift else None
    if record.due_date < today:
        record.status = KipStatus.overdue
    elif shift is None:
        record.status = KipStatus.conflict
    else:
        record.status = KipStatus.planned
    db.flush()
    return record


def recalculate_all_kip_records(db: Session, today: date | None = None) -> int:
    records = db.scalars(select(KipRecord).where(KipRecord.status != KipStatus.completed)).all()
    for record in records:
        recalculate_kip_record(db, record, today=today)
    return len(records)


def upsert_latest_kip_record(
    db: Session,
    *,
    employee_id: int,
    last_kip_date: date,
    source: str | None = None,
    raw_value: str | None = None,
    today: date | None = None,
) -> KipRecord:
    record = db.scalar(select(KipRecord).where(KipRecord.employee_id == employee_id).order_by(KipRecord.id.desc()))
    if record is None:
        return plan_kip_record(
            db,
            employee_id=employee_id,
            last_kip_date=last_kip_date,
            today=today,
            source=source,
            raw_value=raw_value,
        )
    if last_kip_date >= record.last_kip_date:
        record.last_kip_date = last_kip_date
        record.source = source
        record.raw_value = raw_value
    recalculate_kip_record(db, record, today=today)
    return record


def datetime_in_shift(moment: datetime, shift: WorkShift) -> bool:
    if shift.start_datetime and shift.end_datetime:
        return shift.start_datetime <= moment < shift.end_datetime
    return shift.shift_date == moment.date()


def change_kip_date(db: Session, record: KipRecord, new_start: datetime, actor: str = "admin") -> KipRecord:
    if new_start.date() > record.due_date:
        raise ValueError(KIP_LATE_ERROR)
    shift = db.scalar(working_shift_query(record.employee_id, new_start.date()))
    if shift is None or not datetime_in_shift(new_start, shift):
        raise ValueError("КИП должен попадать в рабочее время сотрудника.")

    old_date = record.planned_date
    record.planned_date = new_start.date()
    record.planned_start = new_start
    record.planned_end = min(shift.end_datetime, new_start + timedelta(hours=1)) if shift.end_datetime else None
    record.status = KipStatus.planned
    db.add(
        AuditLog(
            actor=actor,
            action="kip_date_changed",
            entity_type="kip_records",
            entity_id=record.id,
            message=f"Дата КИП изменена с {old_date} на {record.planned_date}",
        )
    )
    db.flush()
    return record


def default_shift_times(shift_date: date, shift_type: ShiftType) -> tuple[datetime | None, datetime | None]:
    if shift_type == ShiftType.day:
        return datetime.combine(shift_date, time(8, 0)), datetime.combine(shift_date, time(20, 0))
    if shift_type == ShiftType.night:
        return datetime.combine(shift_date, time(20, 0)), datetime.combine(shift_date + timedelta(days=1), time(8, 0))
    if shift_type == ShiftType.training:
        return datetime.combine(shift_date, time(9, 0)), datetime.combine(shift_date, time(17, 0))
    return None, None
