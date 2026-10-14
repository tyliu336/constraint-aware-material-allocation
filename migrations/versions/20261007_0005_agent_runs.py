"""Add bounded agent runs, traces, and approval requests.

Revision ID: 20261007_0005
Revises: 20261006_0004
Create Date: 2026-10-07
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261007_0005"
down_revision: str | None = "20261006_0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "agent_runs",
        sa.Column("run_id", sa.String(length=128), nullable=False),
        sa.Column("project_id", sa.String(length=128), nullable=False),
        sa.Column("allocation_task_id", sa.String(length=128), nullable=False),
        sa.Column("caller_user_id", sa.String(length=128), nullable=False),
        sa.Column("model_id", sa.String(length=200), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("goal_hash", sa.String(length=64), nullable=False),
        sa.Column("prompt_name", sa.String(length=128), nullable=False),
        sa.Column("prompt_version", sa.String(length=64), nullable=False),
        sa.Column("prompt_content_sha256", sa.String(length=64), nullable=False),
        sa.Column("budget_payload", sa.JSON(), nullable=False),
        sa.Column("usage_payload", sa.JSON(), nullable=False),
        sa.Column("previous_response_id", sa.String(length=200), nullable=True),
        sa.Column("final_payload", sa.JSON(), nullable=True),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('running','awaiting_approval','success','needs_clarification',"
            "'no_solution','tool_error','budget_exhausted','approval_granted',"
            "'approval_rejected')",
            name="ck_agent_runs_status",
        ),
        sa.ForeignKeyConstraint(
            ["allocation_task_id"], ["allocation_tasks.allocation_task_id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(["project_id"], ["projects.project_id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("run_id"),
    )
    for column in ("project_id", "allocation_task_id", "status"):
        op.create_index(op.f(f"ix_agent_runs_{column}"), "agent_runs", [column])

    op.create_table(
        "agent_traces",
        sa.Column("agent_trace_id", sa.Integer(), nullable=False),
        sa.Column("run_id", sa.String(length=128), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("sequence >= 1", name="ck_agent_trace_sequence_positive"),
        sa.ForeignKeyConstraint(["run_id"], ["agent_runs.run_id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("agent_trace_id"),
        sa.UniqueConstraint("run_id", "sequence", name="uq_agent_trace_run_sequence"),
    )
    op.create_index(op.f("ix_agent_traces_run_id"), "agent_traces", ["run_id"])

    op.create_table(
        "agent_approval_requests",
        sa.Column("approval_request_id", sa.String(length=128), nullable=False),
        sa.Column("run_id", sa.String(length=128), nullable=False),
        sa.Column("action", sa.String(length=64), nullable=False),
        sa.Column("resource_id", sa.String(length=128), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("decided_by", sa.String(length=128), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.CheckConstraint(
            "action IN ('save_allocation_plan','reserve_inventory','approve_plan')",
            name="ck_agent_approval_requests_action",
        ),
        sa.CheckConstraint(
            "status IN ('pending','approved','rejected')",
            name="ck_agent_approval_requests_status",
        ),
        sa.ForeignKeyConstraint(["run_id"], ["agent_runs.run_id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("approval_request_id"),
        sa.UniqueConstraint("run_id"),
    )
    op.create_index(
        op.f("ix_agent_approval_requests_run_id"),
        "agent_approval_requests",
        ["run_id"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_agent_approval_requests_run_id"), table_name="agent_approval_requests")
    op.drop_table("agent_approval_requests")
    op.drop_index(op.f("ix_agent_traces_run_id"), table_name="agent_traces")
    op.drop_table("agent_traces")
    for column in ("status", "allocation_task_id", "project_id"):
        op.drop_index(op.f(f"ix_agent_runs_{column}"), table_name="agent_runs")
    op.drop_table("agent_runs")
