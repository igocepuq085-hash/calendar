from datetime import date, datetime, time, timedelta
from hashlib import sha1

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.models import Employee, EmployeeStatus, EventType, KipStatus, NotificationSetting, ShiftType

try:
    from icalendar import Alarm, Calendar, Event
except ImportError:  # pragma: no cover - Railway installs icalendar; fallback keeps local tests runnable.
    Alarm = Calendar = Event = None


SUMMARY = {
    "day_shift": "🟢 Дневная смена",
    "night_shift": "🔵 Ночная смена",
    "kip": "🟠 КИП",
    "knowledge_check": "🔴 Проверка знаний",
    "medical_check": "🟣 Медицинская комиссия",
}

CORE_KNOWLEDGE_MARKERS = ["по от", "по эб", "строп", "кран", "высот"]


def _uid(prefix: str, item_id: int) -> str:
    digest = sha1(f"{prefix}:{item_id}".encode()).hexdigest()[:12]
    return f"{prefix}-{item_id}-{digest}@kip-calendar-service"


def _db_trigger(setting: NotificationSetting) -> timedelta:
    if setting.unit == "minutes":
        return timedelta(minutes=-setting.amount)
    if setting.unit == "hours":
        return timedelta(hours=-setting.amount)
    return timedelta(days=-setting.amount)


def default_alarm_offsets(event_type: EventType, subtype: str | None = None) -> list[timedelta]:
    """Business default calendar reminders.

    Month reminder is represented as 30 days because iCalendar duration does not
    support calendar months in a portable VALARM trigger.
    """
    normalized = (subtype or "").lower()
    if event_type == EventType.kip:
        return [timedelta(days=-7), timedelta(days=-1)]
    if event_type == EventType.medical_check:
        return [timedelta(days=-30), timedelta(days=-1)]
    if event_type == EventType.knowledge_check:
        if "фильтр" in normalized:
            return [timedelta(days=-30), timedelta(days=-1)]
        if "инструктаж" in normalized:
            return [timedelta(days=-7), timedelta(days=-1)]
        if any(marker in normalized for marker in CORE_KNOWLEDGE_MARKERS):
            return [timedelta(days=-14), timedelta(days=-1)]
        return [timedelta(days=-14), timedelta(days=-1)]
    return []


def _alarm_offsets(
    event_type: EventType,
    settings: list[NotificationSetting],
    subtype: str | None = None,
) -> list[timedelta]:
    offsets = default_alarm_offsets(event_type, subtype)
    offsets.extend(_db_trigger(setting) for setting in settings if setting.event_type == event_type and setting.enabled)
    unique: list[timedelta] = []
    for offset in offsets:
        if offset not in unique:
            unique.append(offset)
    return unique


def _add_alarms(event: Event, event_type: EventType, settings: list[NotificationSetting], subtype: str | None = None) -> None:
    for offset in _alarm_offsets(event_type, settings, subtype):
        alarm = Alarm()
        alarm.add("action", "DISPLAY")
        alarm.add("description", event.get("summary"))
        alarm.add("trigger", offset)
        event.add_component(alarm)


def _all_day_event(summary: str, start_date: date, uid: str) -> Event:
    event = Event()
    event.add("uid", uid)
    event.add("summary", summary)
    event.add("dtstart", start_date)
    event.add("dtend", start_date + timedelta(days=1))
    return event


def _timed_event(summary: str, start: datetime | None, end: datetime | None, fallback_date: date, uid: str) -> Event:
    if start is None:
        start = datetime.combine(fallback_date, time(9, 0))
    if end is None or end <= start:
        end = start + timedelta(hours=1)
    event = Event()
    event.add("uid", uid)
    event.add("summary", summary)
    event.add("dtstart", start)
    event.add("dtend", end)
    return event


def _format_dt(value: date | datetime) -> str:
    if isinstance(value, datetime):
        return value.strftime("%Y%m%dT%H%M%S")
    return value.strftime("%Y%m%d")


def _escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace(",", "\\,").replace(";", "\\;").replace("\n", "\\n")


def _duration_to_ical(offset: timedelta) -> str:
    total_seconds = int(abs(offset.total_seconds()))
    days, remainder = divmod(total_seconds, 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes, _ = divmod(remainder, 60)
    sign = "-" if offset.total_seconds() < 0 else ""
    if days and not hours and not minutes:
        return f"{sign}P{days}D"
    parts = f"{sign}P"
    if days:
        parts += f"{days}D"
    parts += "T"
    if hours:
        parts += f"{hours}H"
    if minutes:
        parts += f"{minutes}M"
    if parts.endswith("T"):
        parts += "0M"
    return parts


def _manual_alarm_lines(event_type: EventType, settings: list[NotificationSetting], subtype: str | None = None) -> list[str]:
    lines: list[str] = []
    for offset in _alarm_offsets(event_type, settings, subtype):
        lines.extend(
            [
                "BEGIN:VALARM",
                "ACTION:DISPLAY",
                f"TRIGGER:{_duration_to_ical(offset)}",
                "DESCRIPTION:Напоминание",
                "END:VALARM",
            ]
        )
    return lines


def _manual_event(
    *,
    uid: str,
    summary: str,
    start: date | datetime,
    end: date | datetime,
    event_type: EventType,
    settings: list[NotificationSetting],
    subtype: str | None = None,
) -> list[str]:
    lines = ["BEGIN:VEVENT", f"UID:{uid}", f"SUMMARY:{_escape(summary)}"]
    if isinstance(start, datetime):
        lines.append(f"DTSTART:{_format_dt(start)}")
        lines.append(f"DTEND:{_format_dt(end)}")
    else:
        lines.append(f"DTSTART;VALUE=DATE:{_format_dt(start)}")
        lines.append(f"DTEND;VALUE=DATE:{_format_dt(end)}")
    lines.extend(_manual_alarm_lines(event_type, settings, subtype))
    lines.append("END:VEVENT")
    return lines


def _load_employee(db: Session, employee: Employee) -> Employee:
    return db.scalar(
        select(Employee)
        .where(Employee.id == employee.id)
        .options(
            selectinload(Employee.work_shifts),
            selectinload(Employee.kip_records),
            selectinload(Employee.knowledge_checks),
            selectinload(Employee.medical_checks),
        )
    )


def _add_employee_events_to_manual_calendar(
    lines: list[str],
    employee: Employee,
    settings: list[NotificationSetting],
    *,
    include_shifts: bool,
    include_employee_name: bool,
) -> None:
    name_suffix = f" - {employee.full_name}" if include_employee_name else ""
    if include_shifts:
        for shift in employee.work_shifts:
            if shift.shift_type not in {ShiftType.day, ShiftType.night}:
                continue
            summary_key = "day_shift" if shift.shift_type == ShiftType.day else "night_shift"
            event_type = EventType.day_shift if shift.shift_type == ShiftType.day else EventType.night_shift
            start = shift.start_datetime or datetime.combine(shift.shift_date, time(9, 0))
            end = shift.end_datetime or start + timedelta(hours=1)
            lines.extend(
                _manual_event(
                    uid=_uid("shift", shift.id),
                    summary=f"{SUMMARY[summary_key]}{name_suffix}",
                    start=start,
                    end=end,
                    event_type=event_type,
                    settings=settings,
                )
            )

    for kip in employee.kip_records:
        if kip.planned_date and kip.status in {KipStatus.planned, KipStatus.overdue}:
            start = kip.planned_start or datetime.combine(kip.planned_date, time(9, 0))
            end = kip.planned_end or start + timedelta(hours=1)
            lines.extend(
                _manual_event(
                    uid=_uid("kip", kip.id),
                    summary=f"{SUMMARY['kip']}{name_suffix}",
                    start=start,
                    end=end,
                    event_type=EventType.kip,
                    settings=settings,
                )
            )

    for check in employee.knowledge_checks:
        lines.extend(
            _manual_event(
                uid=_uid("knowledge", check.id),
                summary=f"{SUMMARY['knowledge_check']} - {check.check_type}{name_suffix}",
                start=check.next_date,
                end=check.next_date + timedelta(days=1),
                event_type=EventType.knowledge_check,
                settings=settings,
                subtype=check.check_type,
            )
        )

    for check in employee.medical_checks:
        lines.extend(
            _manual_event(
                uid=_uid("medical", check.id),
                summary=f"{SUMMARY['medical_check']}{name_suffix}",
                start=check.next_date,
                end=check.next_date + timedelta(days=1),
                event_type=EventType.medical_check,
                settings=settings,
            )
        )


def _add_employee_events_to_calendar(
    calendar: Calendar,
    employee: Employee,
    settings: list[NotificationSetting],
    *,
    include_shifts: bool,
    include_employee_name: bool,
) -> None:
    name_suffix = f" - {employee.full_name}" if include_employee_name else ""
    if include_shifts:
        for shift in employee.work_shifts:
            if shift.shift_type == ShiftType.day:
                event = _timed_event(f"{SUMMARY['day_shift']}{name_suffix}", shift.start_datetime, shift.end_datetime, shift.shift_date, _uid("shift", shift.id))
                _add_alarms(event, EventType.day_shift, settings)
                calendar.add_component(event)
            elif shift.shift_type == ShiftType.night:
                event = _timed_event(f"{SUMMARY['night_shift']}{name_suffix}", shift.start_datetime, shift.end_datetime, shift.shift_date, _uid("shift", shift.id))
                _add_alarms(event, EventType.night_shift, settings)
                calendar.add_component(event)

    for kip in employee.kip_records:
        if kip.planned_date and kip.status in {KipStatus.planned, KipStatus.overdue}:
            event = _timed_event(f"{SUMMARY['kip']}{name_suffix}", kip.planned_start, kip.planned_end, kip.planned_date, _uid("kip", kip.id))
            _add_alarms(event, EventType.kip, settings)
            calendar.add_component(event)

    for check in employee.knowledge_checks:
        event = _all_day_event(f"{SUMMARY['knowledge_check']} - {check.check_type}{name_suffix}", check.next_date, _uid("knowledge", check.id))
        _add_alarms(event, EventType.knowledge_check, settings, check.check_type)
        calendar.add_component(event)

    for check in employee.medical_checks:
        event = _all_day_event(f"{SUMMARY['medical_check']}{name_suffix}", check.next_date, _uid("medical", check.id))
        _add_alarms(event, EventType.medical_check, settings)
        calendar.add_component(event)


def _manual_calendar(name: str) -> list[str]:
    return [
        "BEGIN:VCALENDAR",
        "PRODID:-//KIP Calendar Service//RU",
        "VERSION:2.0",
        "CALSCALE:GREGORIAN",
        f"X-WR-CALNAME:{_escape(name)}",
    ]


def build_employee_calendar(db: Session, employee: Employee) -> bytes:
    employee = _load_employee(db, employee)
    settings = list(db.scalars(select(NotificationSetting)))
    if Calendar is None:
        lines = _manual_calendar(f"Производственный календарь {employee.full_name}")
        _add_employee_events_to_manual_calendar(lines, employee, settings, include_shifts=True, include_employee_name=False)
        lines.append("END:VCALENDAR")
        return ("\r\n".join(lines) + "\r\n").encode("utf-8")

    calendar = Calendar()
    calendar.add("prodid", "-//KIP Calendar Service//RU")
    calendar.add("version", "2.0")
    calendar.add("calscale", "GREGORIAN")
    calendar.add("x-wr-calname", f"Производственный календарь {employee.full_name}")
    _add_employee_events_to_calendar(calendar, employee, settings, include_shifts=True, include_employee_name=False)
    return calendar.to_ical()


def build_admin_calendar(db: Session) -> bytes:
    employees = db.scalars(
        select(Employee)
        .where(Employee.status == EmployeeStatus.active)
        .options(
            selectinload(Employee.kip_records),
            selectinload(Employee.knowledge_checks),
            selectinload(Employee.medical_checks),
        )
        .order_by(Employee.full_name)
    ).all()
    settings = list(db.scalars(select(NotificationSetting)))
    if Calendar is None:
        lines = _manual_calendar("Производственный календарь администратора")
        for employee in employees:
            _add_employee_events_to_manual_calendar(lines, employee, settings, include_shifts=False, include_employee_name=True)
        lines.append("END:VCALENDAR")
        return ("\r\n".join(lines) + "\r\n").encode("utf-8")

    calendar = Calendar()
    calendar.add("prodid", "-//KIP Calendar Service//RU")
    calendar.add("version", "2.0")
    calendar.add("calscale", "GREGORIAN")
    calendar.add("x-wr-calname", "Производственный календарь администратора")
    for employee in employees:
        _add_employee_events_to_calendar(calendar, employee, settings, include_shifts=False, include_employee_name=True)
    return calendar.to_ical()
