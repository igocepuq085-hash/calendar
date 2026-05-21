import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Employee


NAME_ALIASES = {
    "АлексеевД.В.": "Алексеев Д.В.",
    "СамойликД.Н.": "Самойлик Д.Н.",
    "Галиулин Д.Р.": "Галиуллин Д.Р.",
}


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
    employee = None
    if normalized_tab:
        employee = db.scalar(select(Employee).where(Employee.tab_number == normalized_tab))
    if employee is None:
        employee = db.scalar(select(Employee).where(Employee.full_name == normalized_name))
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

