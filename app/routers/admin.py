from __future__ import annotations

import calendar
import shutil
from datetime import date, datetime, timedelta
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.auth import COOKIE_NAME, admin_dependency, make_session_cookie
from app.config import get_admin_calendar_token, get_settings
from app.database import get_db
from app.importer import confirm_import, create_uploaded_file, detect_and_parse
from app.models import (
    AuditLog,
    Employee,
    EmployeeStatus,
    EventType,
    ImportErrorRecord,
    KnowledgeCheck,
    KipRecord,
    KipStatus,
    MedicalCheck,
    NotificationSetting,
    UploadedFile,
    WorkShift,
    new_calendar_token,
)
from app.services.kip import KIP_LATE_ERROR, change_kip_date, recalculate_all_kip_records
from app.services.people import normalize_tab_number
from app.ui import (
    access_badge_class,
    access_card_class,
    access_state_label,
    date_badge_class,
    date_state,
    date_state_label,
    kip_status_class,
    kip_status_label,
    state_rank,
    shift_class,
    shift_label,
)

router = APIRouter(prefix="/admin")
templates = Jinja2Templates(directory="app/templates")
templates.env.filters["date_badge_class"] = date_badge_class
templates.env.filters["date_state_label"] = date_state_label
templates.env.filters["shift_class"] = shift_class
templates.env.filters["shift_label"] = shift_label
templates.env.filters["kip_status_class"] = kip_status_class
templates.env.filters["kip_status_label"] = kip_status_label
templates.env.filters["access_card_class"] = access_card_class
templates.env.filters["access_badge_class"] = access_badge_class
templates.env.filters["access_state_label"] = access_state_label


def render(request: Request, template: str, context: dict, status_code: int = 200) -> HTMLResponse:
    context.setdefault("request", request)
    context.setdefault("settings", get_settings())
    context.setdefault("admin_calendar_url", f"{get_settings().base_url}/cal/admin/{get_admin_calendar_token()}.ics")
    return templates.TemplateResponse(request, template, context, status_code=status_code)


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    return date.fromisoformat(value)


def _parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value)


def _control_events(db: Session, start: date, end: date, limit: int = 100) -> list[dict]:
    events: list[dict] = []
    knowledge = db.scalars(
        select(KnowledgeCheck)
        .join(KnowledgeCheck.employee)
        .options(selectinload(KnowledgeCheck.employee))
        .where(KnowledgeCheck.next_date.between(start, end), Employee.status == EmployeeStatus.active)
        .order_by(KnowledgeCheck.next_date, KnowledgeCheck.check_type)
        .limit(limit)
    ).all()
    for row in knowledge:
        events.append(
            {
                "date": row.next_date,
                "employee": row.employee,
                "title": row.check_type,
                "kind": "Проверка",
            }
        )
    medical = db.scalars(
        select(MedicalCheck)
        .join(MedicalCheck.employee)
        .options(selectinload(MedicalCheck.employee))
        .where(MedicalCheck.next_date.between(start, end), Employee.status == EmployeeStatus.active)
        .order_by(MedicalCheck.next_date)
        .limit(limit)
    ).all()
    for row in medical:
        events.append(
            {
                "date": row.next_date,
                "employee": row.employee,
                "title": "Медицинская комиссия",
                "kind": "Медкомиссия",
            }
        )
    events.sort(key=lambda item: (item["date"], item["employee"].full_name, item["title"]))
    return events[:limit]


def _schedule_until_by_employee(db: Session) -> dict[int, date]:
    return {
        employee_id: max_date
        for employee_id, max_date in db.execute(
            select(WorkShift.employee_id, func.max(WorkShift.shift_date)).group_by(WorkShift.employee_id)
        )
        if max_date is not None
    }


def _employee_access_cards(db: Session, employees: list[Employee]) -> list[dict]:
    employee_ids = [employee.id for employee in employees]
    alerts: dict[int, list[dict]] = {employee.id: [] for employee in employees}
    if not employee_ids:
        return []

    for row in db.scalars(select(KnowledgeCheck).where(KnowledgeCheck.employee_id.in_(employee_ids))):
        state = date_state(row.next_date)
        alerts[row.employee_id].append({"state": state, "date": row.next_date, "title": row.check_type})
    for row in db.scalars(select(MedicalCheck).where(MedicalCheck.employee_id.in_(employee_ids))):
        state = date_state(row.next_date)
        alerts[row.employee_id].append({"state": state, "date": row.next_date, "title": "Медицинская комиссия"})
    for row in db.scalars(select(KipRecord).where(KipRecord.employee_id.in_(employee_ids))):
        state = date_state(row.due_date)
        title = "КИП"
        if row.status == KipStatus.overdue:
            state = "danger"
        alerts[row.employee_id].append({"state": state, "date": row.due_date, "title": title})

    cards: list[dict] = []
    for employee in employees:
        if employee.status == EmployeeStatus.inactive:
            state = "inactive"
            note = "Работник неактивен"
            nearest = None
        else:
            employee_alerts = sorted(alerts.get(employee.id, []), key=lambda item: (-state_rank(item["state"]), item["date"]))
            if employee_alerts:
                nearest = employee_alerts[0]
                state = nearest["state"]
                note = f"{nearest['title']} · {nearest['date']}"
            else:
                state = "muted"
                note = "Проверки не загружены"
                nearest = None
        cards.append({"employee": employee, "state": state, "note": note, "nearest": nearest})
    return cards


def _audit(db: Session, action: str, entity_type: str, entity_id: int, message: str) -> None:
    db.add(AuditLog(action=action, entity_type=entity_type, entity_id=entity_id, message=message))


@router.get("/login", response_class=HTMLResponse)
def login_page(request: Request) -> HTMLResponse:
    return render(request, "login.html", {})


@router.post("/login")
def login(username: str = Form(...), password: str = Form(...)) -> RedirectResponse:
    settings = get_settings()
    if username != settings.admin_username or password != settings.admin_password:
        return RedirectResponse("/admin/login?error=1", status_code=303)
    response = RedirectResponse("/admin", status_code=303)
    response.set_cookie(COOKIE_NAME, make_session_cookie(username), httponly=True, samesite="lax")
    return response


@router.post("/logout")
def logout() -> RedirectResponse:
    response = RedirectResponse("/admin/login", status_code=303)
    response.delete_cookie(COOKIE_NAME)
    return response


@router.get("", response_class=HTMLResponse, dependencies=[Depends(admin_dependency)])
def dashboard(request: Request, db: Session = Depends(get_db)) -> HTMLResponse:
    today = date.today()
    month_start = today.replace(day=1)
    if today.month == 12:
        next_month = today.replace(year=today.year + 1, month=1, day=1)
    else:
        next_month = today.replace(month=today.month + 1, day=1)
    month_end = next_month - timedelta(days=1)
    next_7 = today + timedelta(days=7)
    next_14 = today + timedelta(days=14)
    control_month = _control_events(db, month_start, month_end, limit=120)
    control_week = _control_events(db, today, next_7, limit=80)
    schedule_until = _schedule_until_by_employee(db)
    kip_conflicts = db.scalars(
        select(KipRecord)
        .join(KipRecord.employee)
        .options(selectinload(KipRecord.employee))
        .where(KipRecord.status == KipStatus.conflict, Employee.status == EmployeeStatus.active)
        .limit(100)
    ).all()
    real_kip_conflicts = [row for row in kip_conflicts if schedule_until.get(row.employee_id) and schedule_until[row.employee_id] >= row.due_date]
    waiting_kip_schedule = [row for row in kip_conflicts if not schedule_until.get(row.employee_id) or schedule_until[row.employee_id] < row.due_date]
    context = {
        "kip_soon": db.scalars(select(KipRecord).join(KipRecord.employee).options(selectinload(KipRecord.employee)).where(KipRecord.due_date.between(today, next_14), Employee.status == EmployeeStatus.active).limit(20)).all(),
        "kip_overdue": db.scalars(select(KipRecord).join(KipRecord.employee).options(selectinload(KipRecord.employee)).where(KipRecord.status == KipStatus.overdue, Employee.status == EmployeeStatus.active).limit(20)).all(),
        "kip_conflicts": real_kip_conflicts[:20],
        "waiting_kip_schedule": waiting_kip_schedule[:20],
        "knowledge_month": db.scalars(select(KnowledgeCheck).join(KnowledgeCheck.employee).options(selectinload(KnowledgeCheck.employee)).where(KnowledgeCheck.next_date.between(month_start, month_end), Employee.status == EmployeeStatus.active).order_by(KnowledgeCheck.next_date).limit(80)).all(),
        "knowledge_week": db.scalars(select(KnowledgeCheck).join(KnowledgeCheck.employee).options(selectinload(KnowledgeCheck.employee)).where(KnowledgeCheck.next_date.between(today, next_7), Employee.status == EmployeeStatus.active).order_by(KnowledgeCheck.next_date).limit(40)).all(),
        "control_month": control_month,
        "control_week": control_week,
        "knowledge_soon": db.scalars(select(KnowledgeCheck).options(selectinload(KnowledgeCheck.employee)).where(KnowledgeCheck.next_date.between(today, next_14)).limit(20)).all(),
        "medical_soon": db.scalars(select(MedicalCheck).options(selectinload(MedicalCheck.employee)).where(MedicalCheck.next_date.between(today, next_14)).limit(20)).all(),
        "last_errors": db.scalars(select(ImportErrorRecord).order_by(ImportErrorRecord.id.desc()).limit(10)).all(),
        "month_start": month_start,
        "month_end": month_end,
        "next_7": next_7,
    }
    return render(request, "dashboard.html", context)


@router.get("/employees", response_class=HTMLResponse, dependencies=[Depends(admin_dependency)])
def employees(request: Request, db: Session = Depends(get_db)) -> HTMLResponse:
    items = db.scalars(select(Employee).order_by(Employee.full_name)).all()
    cards = _employee_access_cards(db, items)
    return render(request, "employees.html", {"cards": cards})


@router.get("/employees/{employee_id}", response_class=HTMLResponse, dependencies=[Depends(admin_dependency)])
def employee_card(employee_id: int, request: Request, db: Session = Depends(get_db)) -> HTMLResponse:
    employee = db.scalar(
        select(Employee)
        .where(Employee.id == employee_id)
        .options(
            selectinload(Employee.work_shifts),
            selectinload(Employee.kip_records),
            selectinload(Employee.knowledge_checks),
            selectinload(Employee.medical_checks),
        )
    )
    if employee is None:
        raise HTTPException(status_code=404)
    events = []
    for shift in employee.work_shifts:
        if shift.shift_date >= date.today():
            events.append((shift.shift_date, f"Смена: {shift.shift_type.value}"))
    for kip in employee.kip_records:
        if kip.planned_date and kip.planned_date >= date.today():
            events.append((kip.planned_date, f"КИП: {kip.status.value}"))
    for check in employee.knowledge_checks:
        if check.next_date >= date.today():
            events.append((check.next_date, f"Проверка знаний: {check.check_type}"))
    for check in employee.medical_checks:
        if check.next_date >= date.today():
            events.append((check.next_date, "Медицинская комиссия"))
    events.sort(key=lambda item: item[0])
    future_shifts = sorted([s for s in employee.work_shifts if s.shift_date >= date.today()], key=lambda s: s.shift_date)[:21]
    active_kip = sorted(employee.kip_records, key=lambda k: (k.due_date, k.id))[-1:] if employee.kip_records else []
    knowledge_rows = sorted(employee.knowledge_checks, key=lambda k: (k.next_date, k.check_type))
    medical_rows = sorted(employee.medical_checks, key=lambda m: m.next_date)
    return render(
        request,
        "employee_card.html",
        {
            "employee": employee,
            "events": events[:50],
            "future_shifts": future_shifts,
            "active_kip": active_kip,
            "knowledge_rows": knowledge_rows,
            "medical_rows": medical_rows,
        },
    )


@router.post("/employees/{employee_id}/profile", dependencies=[Depends(admin_dependency)])
def update_employee_profile(
    employee_id: int,
    tab_number: str = Form(""),
    department: str = Form("СЛХ"),
    status: EmployeeStatus = Form(...),
    position: str = Form(""),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    employee = db.get(Employee, employee_id)
    if employee is None:
        raise HTTPException(status_code=404)
    normalized_tab = normalize_tab_number(tab_number)
    if normalized_tab:
        existing = db.scalar(select(Employee).where(Employee.tab_number == normalized_tab, Employee.id != employee.id))
        if existing is not None:
            return RedirectResponse(
                f"/admin/employees/{employee_id}?error=Табельный номер уже назначен работнику {existing.full_name}",
                status_code=303,
            )
    old = f"{employee.department or ''}; {employee.status.value}; {employee.position or ''}"
    employee.tab_number = normalized_tab
    employee.department = department or "СЛХ"
    employee.status = status
    employee.position = position or employee.position
    _audit(
        db,
        "employee_profile_updated",
        "employees",
        employee.id,
        f"Карточка работника изменена: {old} -> {employee.department}; {employee.status.value}; {employee.position or ''}",
    )
    db.commit()
    return RedirectResponse(f"/admin/employees/{employee_id}", status_code=303)


@router.post("/employees/{employee_id}/rotate-token", dependencies=[Depends(admin_dependency)])
def rotate_token(employee_id: int, db: Session = Depends(get_db)) -> RedirectResponse:
    employee = db.get(Employee, employee_id)
    if employee is None:
        raise HTTPException(status_code=404)
    employee.calendar_token = new_calendar_token()
    db.commit()
    return RedirectResponse(f"/admin/employees/{employee_id}", status_code=303)


@router.get("/uploads", response_class=HTMLResponse, dependencies=[Depends(admin_dependency)])
def uploads(request: Request, db: Session = Depends(get_db)) -> HTMLResponse:
    items = db.scalars(select(UploadedFile).order_by(UploadedFile.id.desc()).limit(20)).all()
    return render(request, "uploads.html", {"uploads": items})


@router.post("/uploads/preview", dependencies=[Depends(admin_dependency)])
def upload_preview(file: UploadFile = File(...), db: Session = Depends(get_db)) -> RedirectResponse:
    upload_dir = Path(get_settings().upload_dir)
    upload_dir.mkdir(parents=True, exist_ok=True)
    target = upload_dir / f"{datetime.now().strftime('%Y%m%d%H%M%S')}_{file.filename}"
    with target.open("wb") as out:
        shutil.copyfileobj(file.file, out)
    result = detect_and_parse(target)
    upload = create_uploaded_file(db, original_filename=file.filename, stored_path=str(target), result=result)
    db.commit()
    return RedirectResponse(f"/admin/uploads/{upload.id}/preview", status_code=303)


@router.get("/uploads/{upload_id}/preview", response_class=HTMLResponse, dependencies=[Depends(admin_dependency)])
def upload_preview_page(upload_id: int, request: Request, db: Session = Depends(get_db)) -> HTMLResponse:
    upload = db.get(UploadedFile, upload_id)
    if upload is None:
        raise HTTPException(status_code=404)
    errors = db.scalars(select(ImportErrorRecord).where(ImportErrorRecord.uploaded_file_id == upload.id)).all()
    return render(request, "upload_preview.html", {"upload": upload, "errors": errors})


@router.post("/uploads/{upload_id}/confirm", dependencies=[Depends(admin_dependency)])
def upload_confirm(upload_id: int, db: Session = Depends(get_db)) -> RedirectResponse:
    upload = db.get(UploadedFile, upload_id)
    if upload is None:
        raise HTTPException(status_code=404)
    confirm_import(db, upload)
    db.commit()
    return RedirectResponse("/admin/uploads", status_code=303)


@router.get("/work-shifts", response_class=HTMLResponse, dependencies=[Depends(admin_dependency)])
def work_shifts(request: Request, db: Session = Depends(get_db)) -> HTMLResponse:
    today = date.today()
    year = int(request.query_params.get("year", today.year))
    month = int(request.query_params.get("month", today.month))
    days_count = calendar.monthrange(year, month)[1]
    start = date(year, month, 1)
    end = date(year, month, days_count)
    prev_year, prev_month = (year - 1, 12) if month == 1 else (year, month - 1)
    next_year, next_month = (year + 1, 1) if month == 12 else (year, month + 1)
    shifts = db.scalars(
        select(WorkShift)
        .options(selectinload(WorkShift.employee))
        .where(WorkShift.shift_date.between(start, end))
        .order_by(WorkShift.employee_id, WorkShift.shift_date)
    ).all()
    grouped = {}
    for shift in shifts:
        grouped.setdefault(shift.employee, {})[shift.shift_date.day] = shift
    rows = sorted(grouped.items(), key=lambda item: item[0].full_name)
    return render(
        request,
        "work_shifts.html",
        {
            "year": year,
            "month": month,
            "prev_year": prev_year,
            "prev_month": prev_month,
            "next_year": next_year,
            "next_month": next_month,
            "days": list(range(1, days_count + 1)),
            "rows": rows,
        },
    )


@router.get("/kip", response_class=HTMLResponse, dependencies=[Depends(admin_dependency)])
def kip_page(request: Request, db: Session = Depends(get_db)) -> HTMLResponse:
    rows = db.scalars(select(KipRecord).options(selectinload(KipRecord.employee)).order_by(KipRecord.due_date.desc()).limit(100)).all()
    schedule_until = _schedule_until_by_employee(db)
    for row in rows:
        row.schedule_until = schedule_until.get(row.employee_id)
    return render(request, "kip.html", {"records": rows, "error_text": KIP_LATE_ERROR})


@router.post("/kip/recalculate", dependencies=[Depends(admin_dependency)])
def kip_recalculate(db: Session = Depends(get_db)) -> RedirectResponse:
    recalculate_all_kip_records(db)
    db.commit()
    return RedirectResponse("/admin/kip", status_code=303)


@router.post("/kip/{kip_id}/change-date", dependencies=[Depends(admin_dependency)])
def kip_change_date(
    kip_id: int,
    planned_start: datetime = Form(...),
    return_to: str = Form("/admin/kip"),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    record = db.get(KipRecord, kip_id)
    if record is None:
        raise HTTPException(status_code=404)
    try:
        change_kip_date(db, record, planned_start)
    except ValueError as exc:
        return RedirectResponse(f"{return_to}?error={str(exc)}", status_code=303)
    db.commit()
    return RedirectResponse(return_to, status_code=303)


@router.post("/knowledge/{check_id}/update", dependencies=[Depends(admin_dependency)])
def update_knowledge_check(
    check_id: int,
    previous_date: str = Form(""),
    next_date: str = Form(...),
    return_to: str = Form("/admin/knowledge"),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    check = db.get(KnowledgeCheck, check_id)
    if check is None:
        raise HTTPException(status_code=404)
    old = f"{check.previous_date} -> {check.next_date}"
    check.previous_date = _parse_date(previous_date)
    check.next_date = _parse_date(next_date)
    check.status = "planned"
    _audit(db, "knowledge_check_updated", "knowledge_checks", check.id, f"{check.check_type}: {old} -> {check.previous_date} -> {check.next_date}")
    db.commit()
    return RedirectResponse(return_to, status_code=303)


@router.post("/medical/{check_id}/update", dependencies=[Depends(admin_dependency)])
def update_medical_check(
    check_id: int,
    previous_date: str = Form(""),
    next_date: str = Form(...),
    return_to: str = Form("/admin/medical"),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    check = db.get(MedicalCheck, check_id)
    if check is None:
        raise HTTPException(status_code=404)
    old = f"{check.previous_date} -> {check.next_date}"
    check.previous_date = _parse_date(previous_date)
    check.next_date = _parse_date(next_date)
    check.status = "planned"
    _audit(db, "medical_check_updated", "medical_checks", check.id, f"Медкомиссия: {old} -> {check.previous_date} -> {check.next_date}")
    db.commit()
    return RedirectResponse(return_to, status_code=303)


@router.get("/knowledge", response_class=HTMLResponse, dependencies=[Depends(admin_dependency)])
def knowledge(request: Request, db: Session = Depends(get_db)) -> HTMLResponse:
    rows = db.scalars(
        select(KnowledgeCheck)
        .options(selectinload(KnowledgeCheck.employee))
        .order_by(KnowledgeCheck.next_date, KnowledgeCheck.id)
        .limit(300)
    ).all()
    return render(request, "knowledge.html", {"rows": rows})


@router.get("/medical", response_class=HTMLResponse, dependencies=[Depends(admin_dependency)])
def medical(request: Request, db: Session = Depends(get_db)) -> HTMLResponse:
    rows = db.scalars(
        select(MedicalCheck)
        .options(selectinload(MedicalCheck.employee))
        .order_by(MedicalCheck.next_date, MedicalCheck.id)
        .limit(300)
    ).all()
    return render(request, "medical.html", {"rows": rows})


@router.get("/notifications", response_class=HTMLResponse, dependencies=[Depends(admin_dependency)])
def notifications(request: Request, db: Session = Depends(get_db)) -> HTMLResponse:
    settings = db.scalars(select(NotificationSetting).order_by(NotificationSetting.event_type)).all()
    event_types = [event_type for event_type in EventType if event_type != EventType.instructor_trip]
    return render(request, "notifications.html", {"settings_rows": settings, "event_types": event_types})


@router.post("/notifications", dependencies=[Depends(admin_dependency)])
def add_notification(
    event_type: EventType = Form(...),
    amount: int = Form(...),
    unit: str = Form(...),
    enabled: bool = Form(False),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    db.add(NotificationSetting(event_type=event_type, amount=amount, unit=unit, enabled=enabled))
    db.commit()
    return RedirectResponse("/admin/notifications", status_code=303)


@router.post("/notifications/{setting_id}/delete", dependencies=[Depends(admin_dependency)])
def delete_notification(setting_id: int, db: Session = Depends(get_db)) -> RedirectResponse:
    setting = db.get(NotificationSetting, setting_id)
    if setting:
        db.delete(setting)
        db.commit()
    return RedirectResponse("/admin/notifications", status_code=303)


@router.get("/import-errors", response_class=HTMLResponse, dependencies=[Depends(admin_dependency)])
def import_errors(request: Request, db: Session = Depends(get_db)) -> HTMLResponse:
    rows = db.scalars(select(ImportErrorRecord).order_by(ImportErrorRecord.id.desc()).limit(200)).all()
    return render(request, "import_errors.html", {"errors": rows})
