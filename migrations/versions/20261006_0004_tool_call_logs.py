"""Add audited application tool calls.

Revision ID: 20261006_0004
Revises: 20261005_0003
Create Date: 2026-10-06
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261006_0004"
down_revision: str | None = "20261005_0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "tool_call_logs",
        sa.Column("tool_call_id", sa.String(length=128), nullable=False),
        sa.Column("run_id", sa.String(length=128), nullable=False),
        sa.Column("project_id", sa.String(length=128), nullable=False),
        sa.Column("allocation_task_id", sa.String(length=128), nullable=True),
        sa.Column("tool_name", sa.String(length=128), nullable=False),
        sa.Column("tool_version", sa.String(length=64), nullable=False),
        sa.Column("caller_user_id", sa.String(length=128), nullable=False),
        sa.Column("required_role", sa.String(length=32), nullable=True),
        sa.Column("arguments_hash", sa.String(length=64), nullable=False),
        sa.Column("arguments_payload", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("result_hash", sa.String(length=64), nullable=True),
        sa.Column("result_summary", sa.JSON(), nullable=True),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("duration_ms", sa.Integer(), nullable=False),
        sa.CheckConstraint(
            "status IN ('succeeded','failed','timeout')", name="ck_tool_call_logs_status"
        ),
        sa.CheckConstraint("duration_ms >= 0", name="ck_tool_call_logs_duration"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.project_id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("tool_call_id"),
    )
    for column in ("run_id", "project_id", "allocation_task_id", "tool_name"):
        op.create_index(op.f(f"ix_tool_call_logs_{column}"), "tool_call_logs", [column])


def downgrade() -> None:
    for column in ("tool_name", "allocation_task_id", "project_id", "run_id"):
        op.drop_index(op.f(f"ix_tool_call_logs_{column}"), table_name="tool_call_logs")
    op.drop_table("tool_call_logs")
