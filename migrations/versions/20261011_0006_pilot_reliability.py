"""Add retryable tasks and concurrency-safe inventory reservations.

Revision ID: 20261011_0006
Revises: 20261007_0005
Create Date: 2026-10-11
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261011_0006"
down_revision: str | None = "20261007_0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("allocation_tasks") as batch:
        batch.drop_constraint("ck_allocation_tasks_status", type_="check")
        batch.add_column(
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.text("CURRENT_TIMESTAMP"),
            )
        )
        batch.add_column(
            sa.Column("attempt", sa.Integer(), nullable=False, server_default=sa.text("1"))
        )
        batch.add_column(sa.Column("retry_of_task_id", sa.String(length=128), nullable=True))
        batch.add_column(sa.Column("failure_code", sa.String(length=64), nullable=True))
        batch.add_column(sa.Column("cancellation_reason", sa.Text(), nullable=True))
        batch.create_check_constraint(
            "ck_allocation_tasks_status",
            "status IN ('pending','running','completed','failed','cancelled')",
        )
        batch.create_check_constraint(
            "ck_allocation_tasks_attempt", "attempt >= 1 AND attempt <= 3"
        )
        batch.create_foreign_key(
            "fk_allocation_tasks_retry_of_task_id",
            "allocation_tasks",
            ["retry_of_task_id"],
            ["allocation_task_id"],
            ondelete="RESTRICT",
        )
        batch.create_index("ix_allocation_tasks_retry_of_task_id", ["retry_of_task_id"])

    op.create_table(
        "inventory_reservations",
        sa.Column("reservation_id", sa.String(length=128), nullable=False),
        sa.Column("project_id", sa.String(length=128), nullable=False),
        sa.Column("allocation_plan_id", sa.String(length=128), nullable=False),
        sa.Column("inventory_version_id", sa.String(length=128), nullable=False),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("reserved_by", sa.String(length=128), nullable=False),
        sa.Column("reserved_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("release_idempotency_key", sa.String(length=128), nullable=True),
        sa.Column("released_by", sa.String(length=128), nullable=True),
        sa.Column("released_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('active','released')", name="ck_inventory_reservations_status"
        ),
        sa.ForeignKeyConstraint(
            ["allocation_plan_id"], ["allocation_plans.allocation_plan_id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["inventory_version_id"],
            ["inventory_versions.inventory_version_id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(["project_id"], ["projects.project_id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("reservation_id"),
        sa.UniqueConstraint("allocation_plan_id"),
        sa.UniqueConstraint("idempotency_key"),
        sa.UniqueConstraint("release_idempotency_key"),
    )
    op.create_index(
        "ix_inventory_reservations_project_id",
        "inventory_reservations",
        ["project_id"],
    )
    op.create_index(
        "ix_inventory_reservations_inventory_version_id",
        "inventory_reservations",
        ["inventory_version_id"],
    )
    op.create_table(
        "reserved_materials",
        sa.Column("reservation_id", sa.String(length=128), nullable=False),
        sa.Column("inventory_version_id", sa.String(length=128), nullable=False),
        sa.Column("material_id", sa.String(length=128), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("released_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("status IN ('active','released')", name="ck_reserved_materials_status"),
        sa.ForeignKeyConstraint(
            ["inventory_version_id", "material_id"],
            ["material_items.inventory_version_id", "material_items.material_id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["reservation_id"],
            ["inventory_reservations.reservation_id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("reservation_id", "material_id"),
    )
    op.create_index(
        "uq_reserved_material_active",
        "reserved_materials",
        ["inventory_version_id", "material_id"],
        unique=True,
        sqlite_where=sa.text("status = 'active'"),
        postgresql_where=sa.text("status = 'active'"),
    )


def downgrade() -> None:
    op.drop_index("uq_reserved_material_active", table_name="reserved_materials")
    op.drop_table("reserved_materials")
    op.drop_index(
        "ix_inventory_reservations_inventory_version_id",
        table_name="inventory_reservations",
    )
    op.drop_index("ix_inventory_reservations_project_id", table_name="inventory_reservations")
    op.drop_table("inventory_reservations")
    with op.batch_alter_table("allocation_tasks") as batch:
        batch.drop_index("ix_allocation_tasks_retry_of_task_id")
        batch.drop_constraint("fk_allocation_tasks_retry_of_task_id", type_="foreignkey")
        batch.drop_constraint("ck_allocation_tasks_attempt", type_="check")
        batch.drop_constraint("ck_allocation_tasks_status", type_="check")
        batch.drop_column("cancellation_reason")
        batch.drop_column("failure_code")
        batch.drop_column("retry_of_task_id")
        batch.drop_column("attempt")
        batch.drop_column("updated_at")
        batch.create_check_constraint(
            "ck_allocation_tasks_status",
            "status IN ('pending','running','completed','failed')",
        )
