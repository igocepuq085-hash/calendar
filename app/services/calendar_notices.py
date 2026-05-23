from datetime import datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import delete
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import CalendarNotice

NOTICE_DELAY = timedelta(minutes=55)
NOTICE_DURATION = timedelta(minutes=20)
NOTICE_RETENTION = timedelta(days=3)


def calendar_now() -> datetime:
    try:
        tz = ZoneInfo(get_settings().calendar_timezone)
    except ZoneInfoNotFoundError:
        tz = ZoneInfo("Asia/Yekaterinburg")
    return datetime.now(tz).replace(tzinfo=None)


def create_calendar_notice(
    db: Session,
    *,
    employee_id: int,
    title: str,
    description: str | None = None,
    source: str | None = None,
    now: datetime | None = None,
) -> CalendarNotice:
    now = now or calendar_now()
    notice = CalendarNotice(
        employee_id=employee_id,
        title=title,
        description=description,
        notify_at=now + NOTICE_DELAY,
        expires_at=now + NOTICE_RETENTION,
        source=source,
    )
    db.add(notice)
    return notice


def cleanup_expired_calendar_notices(db: Session, *, now: datetime | None = None) -> int:
    now = now or calendar_now()
    result = db.execute(delete(CalendarNotice).where(CalendarNotice.expires_at < now))
    return int(result.rowcount or 0)
