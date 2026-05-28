import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import CalendarNotice, Employee, InstructorTrip, KipRecord, KnowledgeCheck, MedicalCheck, WorkShift


NAME_ALIASES = {
    "АлексеевД.В.": "Алексеев Д.В.",
    "СамойликД.Н.": "Самойлик Д.Н.",
    "Галиулин Д.Р.": "Галиуллин Д.Р.",
}


def _merge_duplicate_employee(db: Session, *, target: Employee, duplicate: Employee, tab_number: str | None) -> Employee:
    duplicate.tab_number = None
    db.flush()

    existing_shifts = {(shift.shift_date, shift.shift_type) for shift in target.work_shifts}
    for shift in list(duplicate.work_shifts):
        key = (shift.shift_date, shift.shift_type)
        if key in existing_shifts:
            db.delete(shift)
        else:
            shift.employee = target
            existing_shifts.add(key)

    for model in (KipRecord, KnowledgeCheck, MedicalCheck, InstructorTrip, CalendarNotice):
        for row in db.scalars(select(model).where(model.employee_id == duplicate.id)):
            row.employee_id = target.id

    if tab_number and not target.tab_number:
        target.tab_number = tab_number
    if not target.position and duplicate.position:
        target.position = duplicate.position
    if not target.department and duplicate.department:
        target.department = duplicate.department
    db.flush()
    db.delete(duplicate)
    db.flush()
    return target


def normalize_tab_number(value: object) -> str | None:
    if value is None:
        return None
    digits = re.sub(r"\D", "", str(value))
    if not digits:
        return None
    if len(digits) == 7 and digits.startswith("100"):
        digits = digits[3:]
    return digits.lstrip("0") or "0"


def normalize_name(value: object) -> str:
    text = str(value or "").replace("ё", "е").replace("Ё", "Е").strip()
    text = re.sub(r"(?<=[А-Яа-я])(?=[А-Я]\.)", " ", text)
    text = re.sub(r"\s+", " ", text)
    return NAME_ALIASES.get(text, text)


def get_or_create_employee(
    db: Session,
    *,
    full_name: str,
    tab_number: str | None = None,
    position: str | None = None,
    department: str | None = None,
) -> Employee:
    normalized_name = normalize_name(full_name)
    normalized_tab = normalize_tab_number(tab_number)
    employee_by_tab = None
    if normalized_tab:
        employee_by_tab = db.scalar(select(Employee).where(Employee.tab_number == normalized_tab))
    employee_by_name = db.scalar(select(Employee).where(Employee.full_name == normalized_name))
    if employee_by_tab is not None and employee_by_name is not None and employee_by_tab.id != employee_by_name.id:
        if employee_by_name.tab_number is None:
            employee = _merge_duplicate_employee(
                db,
                target=employee_by_name,
                duplicate=employee_by_tab,
                tab_number=normalized_tab,
            )
        else:
            employee = employee_by_tab
    else:
        employee = employee_by_tab or employee_by_name
    if employee is None:
        employee = Employee(
            full_name=normalized_name,
            tab_number=normalized_tab,
            position=position,
            department=department,
        )
        db.add(employee)
        db.flush()
    else:
        if normalized_tab and not employee.tab_number:
            employee.tab_number = normalized_tab
        if position and not employee.position:
            employee.position = position
        if department and not employee.department:
            employee.department = department
    return employee
