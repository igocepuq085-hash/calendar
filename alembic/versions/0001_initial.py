"""initial schema

Revision ID: 0001_initial
Revises:
Create Date: 2026-05-21
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0001_initial"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    employee_status = sa.Enum("active", "inactive", name="employeestatus")
    shift_type = sa.Enum("day", "night", "off", "vacation", "sick", "training", "unknown", name="shifttype")
    kip_status = sa.Enum("planned", "completed", "conflict", "overdue", name="kipstatus")
    uploaded_status = sa.Enum("previewed", "imported", "failed", name="uploadedfilestatus")
    event_type = sa.Enum(
        "kip",
        "knowledge_check",
        "medical_check",
        "day_shift",
        "night_shift",
        "instructor_trip",
        name="eventtype",
    )

    op.create_table(
        "employees",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("full_name", sa.String(length=255), nullable=False),
        sa.Column("tab_number", sa.String(length=32), nullable=True),
        sa.Column("department", sa.String(length=255), nullable=True),
        sa.Column("position", sa.String(length=255), nullable=True),
        sa.Column("status", employee_status, nullable=False),
        sa.Column("calendar_token", sa.String(length=128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("calendar_token"),
        sa.UniqueConstraint("tab_number"),
    )
    op.create_index("ix_employees_full_name", "employees", ["full_name"])
    op.create_index("ix_employees_tab_number", "employees", ["tab_number"])

    op.create_table(
        "uploaded_files",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("original_filename", sa.String(length=255), nullable=False),
        sa.Column("stored_path", sa.String(length=512), nullable=False),
        sa.Column("parser_name", sa.String(length=128), nullable=False),
        sa.Column("status", uploaded_status, nullable=False),
        sa.Column("rows_found", sa.Integer(), nullable=False),
        sa.Column("employees_found", sa.Integer(), nullable=False),
        sa.Column("events_created", sa.Integer(), nullable=False),
        sa.Column("errors_count", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "notification_settings",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("event_type", event_type, nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("amount", sa.Integer(), nullable=False),
        sa.Column("unit", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_notification_settings_event_type", "notification_settings", ["event_type"])

    op.create_table(
        "audit_log",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("actor", sa.String(length=128), nullable=False),
        sa.Column("action", sa.String(length=128), nullable=False),
        sa.Column("entity_type", sa.String(length=128), nullable=False),
        sa.Column("entity_id", sa.Integer(), nullable=True),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_audit_log_action", "audit_log", ["action"])

    op.create_table(
        "work_shifts",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("employee_id", sa.Integer(), nullable=False),
        sa.Column("shift_date", sa.Date(), nullable=False),
        sa.Column("shift_type", shift_type, nullable=False),
        sa.Column("start_datetime", sa.DateTime(timezone=True), nullable=True),
        sa.Column("end_datetime", sa.DateTime(timezone=True), nullable=True),
        sa.Column("raw_value", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.ForeignKeyConstraint(["employee_id"], ["employees.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("employee_id", "shift_date", "shift_type", name="uq_employee_shift_date_type"),
    )
    op.create_index("ix_work_shifts_employee_id", "work_shifts", ["employee_id"])
    op.create_index("ix_work_shifts_shift_date", "work_shifts", ["shift_date"])
    op.create_index("ix_work_shifts_shift_type", "work_shifts", ["shift_type"])

    op.create_table(
        "kip_records",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("employee_id", sa.Integer(), nullable=False),
        sa.Column("last_kip_date", sa.Date(), nullable=False),
        sa.Column("due_date", sa.Date(), nullable=False),
        sa.Column("planned_date", sa.Date(), nullable=True),
        sa.Column("planned_start", sa.DateTime(timezone=True), nullable=True),
        sa.Column("planned_end", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", kip_status, nullable=False),
        sa.Column("source", sa.String(length=255), nullable=True),
        sa.Column("raw_value", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.ForeignKeyConstraint(["employee_id"], ["employees.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_kip_records_due_date", "kip_records", ["due_date"])
    op.create_index("ix_kip_records_employee_id", "kip_records", ["employee_id"])
    op.create_index("ix_kip_records_planned_date", "kip_records", ["planned_date"])
    op.create_index("ix_kip_records_status", "kip_records", ["status"])

    for table_name, date_column in [
        ("knowledge_checks", "next_date"),
        ("medical_checks", "next_date"),
    ]:
        columns = [
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("employee_id", sa.Integer(), nullable=False),
            sa.Column("previous_date", sa.Date(), nullable=True),
            sa.Column("next_date", sa.Date(), nullable=False),
            sa.Column("status", sa.String(length=32), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
            sa.ForeignKeyConstraint(["employee_id"], ["employees.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
        ]
        if table_name == "knowledge_checks":
            columns.insert(2, sa.Column("check_type", sa.String(length=128), nullable=False))
        op.create_table(table_name, *columns)
        op.create_index(f"ix_{table_name}_employee_id", table_name, ["employee_id"])
        op.create_index(f"ix_{table_name}_{date_column}", table_name, [date_column])

    op.create_table(
        "instructor_trips",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("employee_id", sa.Integer(), nullable=False),
        sa.Column("trip_date", sa.Date(), nullable=False),
        sa.Column("start_datetime", sa.DateTime(timezone=True), nullable=True),
        sa.Column("end_datetime", sa.DateTime(timezone=True), nullable=True),
        sa.Column("description", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.ForeignKeyConstraint(["employee_id"], ["employees.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_instructor_trips_employee_id", "instructor_trips", ["employee_id"])
    op.create_index("ix_instructor_trips_trip_date", "instructor_trips", ["trip_date"])

    op.create_table(
        "import_errors",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("uploaded_file_id", sa.Integer(), nullable=True),
        sa.Column("row_number", sa.Integer(), nullable=True),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("raw_data", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.ForeignKeyConstraint(["uploaded_file_id"], ["uploaded_files.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_import_errors_uploaded_file_id", "import_errors", ["uploaded_file_id"])


def downgrade() -> None:
    for table in [
        "import_errors",
        "instructor_trips",
        "medical_checks",
        "knowledge_checks",
        "kip_records",
        "work_shifts",
        "audit_log",
        "notification_settings",
        "uploaded_files",
        "employees",
    ]:
        op.drop_table(table)
    for enum_name in ["eventtype", "uploadedfilestatus", "kipstatus", "shifttype", "employeestatus"]:
        sa.Enum(name=enum_name).drop(op.get_bind(), checkfirst=True)

