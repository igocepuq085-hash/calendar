from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_admin_calendar_token
from app.database import get_db
from app.models import Employee, EmployeeStatus
from app.services.ics import build_admin_calendar, build_employee_calendar

router = APIRouter()


ICS_HEADERS = {
    "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
    "Pragma": "no-cache",
    "Expires": "0",
    "X-Accel-Buffering": "no",
}


@router.get("/cal/{token}.ics")
def calendar(token: str, db: Session = Depends(get_db)) -> Response:
    employee = db.scalar(select(Employee).where(Employee.calendar_token == token))
    if employee is None or employee.status == EmployeeStatus.inactive:
        raise HTTPException(status_code=404, detail="Calendar not found")
    content = build_employee_calendar(db, employee)
    return Response(
        content=content,
        media_type="text/calendar; charset=utf-8",
        headers={**ICS_HEADERS, "Content-Disposition": f'inline; filename="{employee.id}.ics"'},
    )


@router.get("/cal/admin/{token}.ics")
def admin_calendar(token: str, db: Session = Depends(get_db)) -> Response:
    if token != get_admin_calendar_token():
        raise HTTPException(status_code=404, detail="Calendar not found")
    content = build_admin_calendar(db)
    return Response(
        content=content,
        media_type="text/calendar; charset=utf-8",
        headers={**ICS_HEADERS, "Content-Disposition": 'inline; filename="admin.ics"'},
    )
