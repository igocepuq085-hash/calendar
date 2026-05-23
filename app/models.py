from __future__ import annotations

import enum
import secrets
from datetime import date, datetime

from sqlalchemy import Boolean, Date, DateTime, Enum, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from app.database import Base


class EmployeeStatus(str, enum.Enum):
    active = "active"
    inactive = "inactive"


class ShiftType(str, enum.Enum):
    day = "day"
    night = "night"
    off = "off"
    vacation = "vacation"
    sick = "sick"
    training = "training"
    unknown = "unknown"


class KipStatus(str, enum.Enum):
    planned = "planned"
    completed = "completed"
    conflict = "conflict"
    overdue = "overdue"


class UploadedFileStatus(str, enum.Enum):
    previewed = "previewed"
    imported = "imported"
    failed = "failed"


class EventType(str, enum.Enum):
    kip = "kip"
    knowledge_check = "knowledge_check"
    medical_check = "medical_check"
    day_shift = "day_shift"
    night_shift = "night_shift"
    instructor_trip = "instructor_trip"


def new_calendar_token() -> str:
    return secrets.token_urlsafe(32)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class Employee(TimestampMixin, Base):
    __tablename__ = "employees"

    id: Mapped[int] = mapped_column(primary_key=True)
    full_name: Mapped[str] = mapped_column(String(255), index=True)
    tab_number: Mapped[str | None] = mapped_column(String(32), unique=True, index=True)
    department: Mapped[str | None] = mapped_column(String(255))
    position: Mapped[str | None] = mapped_column(String(255))
    status: Mapped[EmployeeStatus] = mapped_column(Enum(EmployeeStatus), default=EmployeeStatus.active)
    calendar_token: Mapped[str] = mapped_column(String(128), unique=True, default=new_calendar_token)

    work_shifts: Mapped[list[WorkShift]] = relationship(back_populates="employee", cascade="all, delete-orphan")
    kip_records: Mapped[list[KipRecord]] = relationship(back_populates="employee", cascade="all, delete-orphan")
    knowledge_checks: Mapped[list[KnowledgeCheck]] = relationship(back_populates="employee", cascade="all, delete-orphan")
    medical_checks: Mapped[list[MedicalCheck]] = relationship(back_populates="employee", cascade="all, delete-orphan")
    instructor_trips: Mapped[list[InstructorTrip]] = relationship(back_populates="employee", cascade="all, delete-orphan")
    calendar_notices: Mapped[list[CalendarNotice]] = relationship(back_populates="employee", cascade="all, delete-orphan")


class WorkShift(TimestampMixin, Base):
    __tablename__ = "work_shifts"
    __table_args__ = (UniqueConstraint("employee_id", "shift_date", "shift_type", name="uq_employee_shift_date_type"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    employee_id: Mapped[int] = mapped_column(ForeignKey("employees.id", ondelete="CASCADE"), index=True)
    shift_date: Mapped[date] = mapped_column(Date, index=True)
    shift_type: Mapped[ShiftType] = mapped_column(Enum(ShiftType), index=True)
    start_datetime: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    end_datetime: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    raw_value: Mapped[str | None] = mapped_column(String(64))

    employee: Mapped[Employee] = relationship(back_populates="work_shifts")


class KipRecord(TimestampMixin, Base):
    __tablename__ = "kip_records"

    id: Mapped[int] = mapped_column(primary_key=True)
    employee_id: Mapped[int] = mapped_column(ForeignKey("employees.id", ondelete="CASCADE"), index=True)
    last_kip_date: Mapped[date] = mapped_column(Date)
    due_date: Mapped[date] = mapped_column(Date, index=True)
    planned_date: Mapped[date | None] = mapped_column(Date, index=True)
    planned_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    planned_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[KipStatus] = mapped_column(Enum(KipStatus), default=KipStatus.planned, index=True)
    source: Mapped[str | None] = mapped_column(String(255))
    raw_value: Mapped[str | None] = mapped_column(String(255))

    employee: Mapped[Employee] = relationship(back_populates="kip_records")


class KnowledgeCheck(TimestampMixin, Base):
    __tablename__ = "knowledge_checks"

    id: Mapped[int] = mapped_column(primary_key=True)
    employee_id: Mapped[int] = mapped_column(ForeignKey("employees.id", ondelete="CASCADE"), index=True)
    check_type: Mapped[str] = mapped_column(String(128))
    previous_date: Mapped[date | None] = mapped_column(Date)
    next_date: Mapped[date] = mapped_column(Date, index=True)
    status: Mapped[str] = mapped_column(String(32), default="planned")

    employee: Mapped[Employee] = relationship(back_populates="knowledge_checks")


class MedicalCheck(TimestampMixin, Base):
    __tablename__ = "medical_checks"

    id: Mapped[int] = mapped_column(primary_key=True)
    employee_id: Mapped[int] = mapped_column(ForeignKey("employees.id", ondelete="CASCADE"), index=True)
    previous_date: Mapped[date | None] = mapped_column(Date)
    next_date: Mapped[date] = mapped_column(Date, index=True)
    status: Mapped[str] = mapped_column(String(32), default="planned")

    employee: Mapped[Employee] = relationship(back_populates="medical_checks")


class InstructorTrip(TimestampMixin, Base):
    __tablename__ = "instructor_trips"

    id: Mapped[int] = mapped_column(primary_key=True)
    employee_id: Mapped[int] = mapped_column(ForeignKey("employees.id", ondelete="CASCADE"), index=True)
    trip_date: Mapped[date] = mapped_column(Date, index=True)
    start_datetime: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    end_datetime: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    description: Mapped[str | None] = mapped_column(String(255))

    employee: Mapped[Employee] = relationship(back_populates="instructor_trips")


class CalendarNotice(TimestampMixin, Base):
    __tablename__ = "calendar_notices"

    id: Mapped[int] = mapped_column(primary_key=True)
    employee_id: Mapped[int] = mapped_column(ForeignKey("employees.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(255))
    description: Mapped[str | None] = mapped_column(Text)
    notify_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    source: Mapped[str | None] = mapped_column(String(128))

    employee: Mapped[Employee] = relationship(back_populates="calendar_notices")


class UploadedFile(TimestampMixin, Base):
    __tablename__ = "uploaded_files"

    id: Mapped[int] = mapped_column(primary_key=True)
    original_filename: Mapped[str] = mapped_column(String(255))
    stored_path: Mapped[str] = mapped_column(String(512))
    parser_name: Mapped[str] = mapped_column(String(128))
    status: Mapped[UploadedFileStatus] = mapped_column(Enum(UploadedFileStatus), default=UploadedFileStatus.previewed)
    rows_found: Mapped[int] = mapped_column(Integer, default=0)
    employees_found: Mapped[int] = mapped_column(Integer, default=0)
    events_created: Mapped[int] = mapped_column(Integer, default=0)
    errors_count: Mapped[int] = mapped_column(Integer, default=0)


class ImportErrorRecord(TimestampMixin, Base):
    __tablename__ = "import_errors"

    id: Mapped[int] = mapped_column(primary_key=True)
    uploaded_file_id: Mapped[int | None] = mapped_column(ForeignKey("uploaded_files.id", ondelete="SET NULL"), index=True)
    row_number: Mapped[int | None] = mapped_column(Integer)
    message: Mapped[str] = mapped_column(Text)
    raw_data: Mapped[str | None] = mapped_column(Text)


class NotificationSetting(TimestampMixin, Base):
    __tablename__ = "notification_settings"

    id: Mapped[int] = mapped_column(primary_key=True)
    event_type: Mapped[EventType] = mapped_column(Enum(EventType), index=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    amount: Mapped[int] = mapped_column(Integer)
    unit: Mapped[str] = mapped_column(String(16))


class AuditLog(TimestampMixin, Base):
    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(primary_key=True)
    actor: Mapped[str] = mapped_column(String(128), default="admin")
    action: Mapped[str] = mapped_column(String(128), index=True)
    entity_type: Mapped[str] = mapped_column(String(128))
    entity_id: Mapped[int | None] = mapped_column(Integer)
    message: Mapped[str] = mapped_column(Text)
