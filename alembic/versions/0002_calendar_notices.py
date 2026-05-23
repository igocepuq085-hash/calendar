"""calendar notices

Revision ID: 0002_calendar_notices
Revises: 0001_initial
Create Date: 2026-05-23
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0002_calendar_notices"
down_revision: Union[str, None] = "0001_initial"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "calendar_notices",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("employee_id", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("notify_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source", sa.String(length=128), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.ForeignKeyConstraint(["employee_id"], ["employees.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_calendar_notices_employee_id", "calendar_notices", ["employee_id"])
    op.create_index("ix_calendar_notices_notify_at", "calendar_notices", ["notify_at"])
    op.create_index("ix_calendar_notices_expires_at", "calendar_notices", ["expires_at"])


def downgrade() -> None:
    op.drop_index("ix_calendar_notices_expires_at", table_name="calendar_notices")
    op.drop_index("ix_calendar_notices_notify_at", table_name="calendar_notices")
    op.drop_index("ix_calendar_notices_employee_id", table_name="calendar_notices")
    op.drop_table("calendar_notices")
