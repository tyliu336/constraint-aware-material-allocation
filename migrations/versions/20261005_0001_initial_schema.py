"""Initial product persistence schema.

Revision ID: 20261005_0001
Revises:
Create Date: 2026-10-05
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261005_0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "projects",
        sa.Column("project_id", sa.String(length=128), nullable=False),
        sa.Column("name", sa.String(length=300), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("project_id"),
    )
    op.create_table(
        "inventory_versions",
        sa.Column("inventory_version_id", sa.String(length=128), nullable=False),
        sa.Column("project_id", sa.String(length=128), nullable=False),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.Column("source_system", sa.String(length=128), nullable=False),
        sa.Column("source_path", sa.Text(), nullable=False),
        sa.Column("source_sha256", sa.String(length=64), nullable=False),
        sa.Column("snapshot_hash", sa.String(length=64), nullable=False),
        sa.Column("item_count", sa.Integer(), nullable=False),
        sa.Column("imported_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("item_count > 0", name="ck_inventory_versions_item_count_positive"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.project_id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("inventory_version_id"),
        sa.UniqueConstraint("idempotency_key"),
    )
    op.create_index(
        op.f("ix_inventory_versions_project_id"),
        "inventory_versions",
        ["project_id"],
        unique=False,
    )
    op.create_table(
        "material_items",
        sa.Column("inventory_version_id", sa.String(length=128), nullable=False),
        sa.Column("material_id", sa.String(length=128), nullable=False),
        sa.Column("source_row_sha256", sa.String(length=64), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(
            ["inventory_version_id"],
            ["inventory_versions.inventory_version_id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("inventory_version_id", "material_id"),
    )
    op.create_table(
        "requirements",
        sa.Column("requirement_id", sa.String(length=128), nullable=False),
        sa.Column("project_id", sa.String(length=128), nullable=False),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.Column("slot_requirement_version_id", sa.String(length=128), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("confirmed_by", sa.String(length=128), nullable=False),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.project_id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("requirement_id"),
        sa.UniqueConstraint("idempotency_key"),
    )
    op.create_index(
        op.f("ix_requirements_project_id"), "requirements", ["project_id"], unique=False
    )
    op.create_table(
        "slot_requirements",
        sa.Column("requirement_id", sa.String(length=128), nullable=False),
        sa.Column("slot_id", sa.String(length=128), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(
            ["requirement_id"], ["requirements.requirement_id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("requirement_id", "slot_id"),
    )
    op.create_table(
        "allocation_tasks",
        sa.Column("allocation_task_id", sa.String(length=128), nullable=False),
        sa.Column("project_id", sa.String(length=128), nullable=False),
        sa.Column("inventory_version_id", sa.String(length=128), nullable=False),
        sa.Column("requirement_id", sa.String(length=128), nullable=False),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('pending','running','completed','failed')",
            name="ck_allocation_tasks_status",
        ),
        sa.ForeignKeyConstraint(
            ["inventory_version_id"],
            ["inventory_versions.inventory_version_id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(["project_id"], ["projects.project_id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["requirement_id"], ["requirements.requirement_id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("allocation_task_id"),
        sa.UniqueConstraint("idempotency_key"),
    )
    op.create_index(
        op.f("ix_allocation_tasks_project_id"),
        "allocation_tasks",
        ["project_id"],
        unique=False,
    )
    op.create_table(
        "allocation_plans",
        sa.Column("allocation_plan_id", sa.String(length=128), nullable=False),
        sa.Column("allocation_task_id", sa.String(length=128), nullable=False),
        sa.Column("plan_version", sa.Integer(), nullable=False),
        sa.Column("terminal_status", sa.String(length=30), nullable=False),
        sa.Column("input_snapshot_hash", sa.String(length=64), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("plan_version >= 1", name="ck_allocation_plans_version_positive"),
        sa.CheckConstraint(
            "terminal_status IN "
            "('complete_feasible','requires_review','infeasible','input_invalid','budget_exhausted')",
            name="ck_allocation_plans_terminal_status",
        ),
        sa.ForeignKeyConstraint(
            ["allocation_task_id"],
            ["allocation_tasks.allocation_task_id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("allocation_plan_id"),
        sa.UniqueConstraint(
            "allocation_task_id", "plan_version", name="uq_allocation_plan_task_version"
        ),
    )
    op.create_index(
        op.f("ix_allocation_plans_allocation_task_id"),
        "allocation_plans",
        ["allocation_task_id"],
        unique=False,
    )
    op.create_table(
        "rule_evaluations",
        sa.Column("rule_evaluation_id", sa.Integer(), nullable=False),
        sa.Column("allocation_plan_id", sa.String(length=128), nullable=False),
        sa.Column("material_id", sa.String(length=128), nullable=True),
        sa.Column("slot_id", sa.String(length=128), nullable=True),
        sa.Column("rule_id", sa.String(length=128), nullable=False),
        sa.Column("rule_version", sa.String(length=64), nullable=False),
        sa.Column("outcome", sa.String(length=30), nullable=False),
        sa.Column("severity", sa.String(length=30), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.CheckConstraint(
            "outcome IN ('pass','fail','not_evaluable')",
            name="ck_rule_evaluations_outcome",
        ),
        sa.CheckConstraint(
            "severity IN ('blocking','advisory')",
            name="ck_rule_evaluations_severity",
        ),
        sa.ForeignKeyConstraint(
            ["allocation_plan_id"],
            ["allocation_plans.allocation_plan_id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("rule_evaluation_id"),
    )
    op.create_index(
        op.f("ix_rule_evaluations_allocation_plan_id"),
        "rule_evaluations",
        ["allocation_plan_id"],
        unique=False,
    )
    op.create_table(
        "approvals",
        sa.Column("approval_id", sa.String(length=128), nullable=False),
        sa.Column("allocation_plan_id", sa.String(length=128), nullable=False),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.Column("decision", sa.String(length=20), nullable=False),
        sa.Column("decided_by", sa.String(length=128), nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.CheckConstraint("decision IN ('approved','rejected')", name="ck_approvals_decision"),
        sa.ForeignKeyConstraint(
            ["allocation_plan_id"],
            ["allocation_plans.allocation_plan_id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("approval_id"),
        sa.UniqueConstraint("allocation_plan_id"),
        sa.UniqueConstraint("idempotency_key"),
    )


def downgrade() -> None:
    op.drop_table("approvals")
    op.drop_index(op.f("ix_rule_evaluations_allocation_plan_id"), table_name="rule_evaluations")
    op.drop_table("rule_evaluations")
    op.drop_index(op.f("ix_allocation_plans_allocation_task_id"), table_name="allocation_plans")
    op.drop_table("allocation_plans")
    op.drop_index(op.f("ix_allocation_tasks_project_id"), table_name="allocation_tasks")
    op.drop_table("allocation_tasks")
    op.drop_table("slot_requirements")
    op.drop_index(op.f("ix_requirements_project_id"), table_name="requirements")
    op.drop_table("requirements")
    op.drop_table("material_items")
    op.drop_index(op.f("ix_inventory_versions_project_id"), table_name="inventory_versions")
    op.drop_table("inventory_versions")
    op.drop_table("projects")
