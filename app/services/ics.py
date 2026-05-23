from datetime import date, datetime, time, timedelta, timezone
from hashlib import sha1
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.config import get_settings
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
SHIFT_MARKER_DURATION = timedelta(hours=1)
ICS_EVENT_VERSION = 2


def _uid(prefix: str, item_id: int) -> str:
    digest = sha1(f"{prefix}:{item_id}".encode()).hexdigest()[:12]
    return f"{prefix}-{item_id}-{digest}@kip-calendar-service"


def _stable_shift_uid(employee_id: int, shift_date: date, shift_type: ShiftType) -> str:
    key = f"shift:{employee_id}:{shift_date.isoformat()}:{shift_type.value}"
    digest = sha1(key.encode()).hexdigest()[:12]
    return f"shift-{employee_id}-{shift_date.strftime('%Y%m%d')}-{shift_type.value}-{digest}@kip-calendar-service"


def _calendar_tz_name() -> str:
    return get_settings().calendar_timezone


def _calendar_tz() -> ZoneInfo:
    try:
        return ZoneInfo(_calendar_tz_name())
    except ZoneInfoNotFoundError:
        return ZoneInfo("Asia/Yekaterinburg")


def _local_wall_datetime(value: datetime) -> datetime:
    return value.replace(tzinfo=None).replace(tzinfo=_calendar_tz())


def _calendar_datetime(value: datetime | None, fallback_date: date, fallback_time: time) -> datetime:
    if value is None:
        value = datetime.combine(fallback_date, fallback_time)
    return _local_wall_datetime(value)


def _shift_marker_times(shift_date: date, shift_type: ShiftType) -> tuple[datetime, datetime]:
    marker_time = time(20, 0) if shift_type == ShiftType.night else time(8, 0)
    start = datetime.combine(shift_date, marker_time).replace(tzinfo=_calendar_tz())
    return start, start + SHIFT_MARKER_DURATION


def _short_marker_times(start: datetime | None, fallback_date: date, fallback_time: time) -> tuple[datetime, datetime]:
    local_start = _calendar_datetime(start, fallback_date, fallback_time)
    return local_start, local_start + SHIFT_MARKER_DURATION


def _as_utc(value: datetime | None) -> datetime:
    if value is None:
        return datetime.now(timezone.utc)
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _event_stamp(*items: object) -> datetime:
    stamps: list[datetime] = []
    for item in items:
        updated_at = getattr(item, "updated_at", None)
        created_at = getattr(item, "created_at", None)
        if updated_at is not None:
            stamps.append(_as_utc(updated_at))
        elif created_at is not None:
            stamps.append(_as_utc(created_at))
    return max(stamps) if stamps else datetime.now(timezone.utc)


def _sequence(stamp: datetime) -> int:
    return max(0, int(stamp.timestamp())) + ICS_EVENT_VERSION


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


def _add_event_metadata(event: Event, stamp: datetime) -> None:
    event.add("dtstamp", datetime.now(timezone.utc))
    event.add("last-modified", stamp)
    event.add("sequence", _sequence(stamp))


def _all_day_event(summary: str, start_date: date, uid: str, stamp: datetime) -> Event:
    event = Event()
    event.add("uid", uid)
    event.add("summary", summary)
    event.add("dtstart", start_date)
    event.add("dtend", start_date + timedelta(days=1))
    _add_event_metadata(event, stamp)
    return event


def _timed_event(summary: str, start: datetime | None, end: datetime | None, fallback_date: date, uid: str, stamp: datetime) -> Event:
    if start is None:
        start = datetime.combine(fallback_date, time(9, 0))
    if end is None or end <= start:
        end = start + timedelta(hours=1)
    event = Event()
    event.add("uid", uid)
    event.add("summary", summary)
    event.add("dtstart", start)
    event.add("dtend", end)
    _add_event_metadata(event, stamp)
    return event


def _format_dt(value: date | datetime) -> str:
    if isinstance(value, datetime):
        return value.strftime("%Y%m%dT%H%M%S")
    return value.strftime("%Y%m%d")


def _format_utc_dt(value: datetime) -> str:
    return _as_utc(value).strftime("%Y%m%dT%H%M%SZ")


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
    stamp: datetime | None = None,
) -> list[str]:
    stamp = stamp or datetime.now(timezone.utc)
    lines = [
        "BEGIN:VEVENT",
        f"UID:{uid}",
        f"SUMMARY:{_escape(summary)}",
        f"DTSTAMP:{_format_utc_dt(datetime.now(timezone.utc))}",
        f"LAST-MODIFIED:{_format_utc_dt(stamp)}",
        f"SEQUENCE:{_sequence(stamp)}",
    ]
    if isinstance(start, datetime):
        lines.append(f"DTSTART;TZID={_calendar_tz_name()}:{_format_dt(start)}")
        lines.append(f"DTEND;TZID={_calendar_tz_name()}:{_format_dt(end)}")
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
            start, end = _shift_marker_times(shift.shift_date, shift.shift_type)
            lines.extend(
                _manual_event(
                    uid=_stable_shift_uid(shift.employee_id, shift.shift_date, shift.shift_type),
                    summary=f"{SUMMARY[summary_key]}{name_suffix}",
                    start=start,
                    end=end,
                    event_type=event_type,
                    settings=settings,
                    stamp=_event_stamp(shift),
                )
            )

    for kip in employee.kip_records:
        if kip.planned_date and kip.status in {KipStatus.planned, KipStatus.overdue}:
            start, end = _short_marker_times(kip.planned_start, kip.planned_date, time(9, 0))
            lines.extend(
                _manual_event(
                    uid=_uid("kip", kip.id),
                    summary=f"{SUMMARY['kip']}{name_suffix}",
                    start=start,
                    end=end,
                    event_type=EventType.kip,
                    settings=settings,
                    stamp=_event_stamp(kip),
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
                stamp=_event_stamp(check),
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
                stamp=_event_stamp(check),
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
                start, end = _shift_marker_times(shift.shift_date, shift.shift_type)
                event = _timed_event(
                    f"{SUMMARY['day_shift']}{name_suffix}",
                    start,
                    end,
                    shift.shift_date,
                    _stable_shift_uid(shift.employee_id, shift.shift_date, shift.shift_type),
                    _event_stamp(shift),
                )
                _add_alarms(event, EventType.day_shift, settings)
                calendar.add_component(event)
            elif shift.shift_type == ShiftType.night:
                start, end = _shift_marker_times(shift.shift_date, shift.shift_type)
                event = _timed_event(
                    f"{SUMMARY['night_shift']}{name_suffix}",
                    start,
                    end,
                    shift.shift_date,
                    _stable_shift_uid(shift.employee_id, shift.shift_date, shift.shift_type),
                    _event_stamp(shift),
                )
                _add_alarms(event, EventType.night_shift, settings)
                calendar.add_component(event)

    for kip in employee.kip_records:
        if kip.planned_date and kip.status in {KipStatus.planned, KipStatus.overdue}:
            start, end = _short_marker_times(kip.planned_start, kip.planned_date, time(9, 0))
            event = _timed_event(f"{SUMMARY['kip']}{name_suffix}", start, end, kip.planned_date, _uid("kip", kip.id), _event_stamp(kip))
            _add_alarms(event, EventType.kip, settings)
            calendar.add_component(event)

    for check in employee.knowledge_checks:
        event = _all_day_event(f"{SUMMARY['knowledge_check']} - {check.check_type}{name_suffix}", check.next_date, _uid("knowledge", check.id), _event_stamp(check))
        _add_alarms(event, EventType.knowledge_check, settings, check.check_type)
        calendar.add_component(event)

    for check in employee.medical_checks:
        event = _all_day_event(f"{SUMMARY['medical_check']}{name_suffix}", check.next_date, _uid("medical", check.id), _event_stamp(check))
        _add_alarms(event, EventType.medical_check, settings)
        calendar.add_component(event)


def _manual_calendar(name: str) -> list[str]:
    return [
        "BEGIN:VCALENDAR",
        "PRODID:-//KIP Calendar Service//RU",
        "VERSION:2.0",
        "CALSCALE:GREGORIAN",
        f"X-WR-TIMEZONE:{_calendar_tz_name()}",
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
    calendar.add("x-wr-timezone", _calendar_tz_name())
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
    calendar.add("x-wr-timezone", _calendar_tz_name())
    calendar.add("x-wr-calname", "Производственный календарь администратора")
    for employee in employees:
        _add_employee_events_to_calendar(calendar, employee, settings, include_shifts=False, include_employee_name=True)
    return calendar.to_ical()
