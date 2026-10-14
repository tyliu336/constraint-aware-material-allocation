"""Add reconstructable model context snapshots.

Revision ID: 20261005_0003
Revises: 20261005_0002
Create Date: 2026-10-05
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261005_0003"
down_revision: str | None = "20261005_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "requirement_draft_revisions",
        sa.Column(
            "context_snapshot_ids",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'[]'"),
        ),
    )
    op.create_table(
        "context_snapshots",
        sa.Column("context_snapshot_id", sa.String(length=128), nullable=False),
        sa.Column("context_hash", sa.String(length=64), nullable=False),
        sa.Column("project_id", sa.String(length=128), nullable=False),
        sa.Column("draft_id", sa.String(length=128), nullable=False),
        sa.Column("draft_revision", sa.Integer(), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("purpose", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("prompt_name", sa.String(length=128), nullable=False),
        sa.Column("prompt_version", sa.String(length=64), nullable=False),
        sa.Column("prompt_content_sha256", sa.String(length=64), nullable=False),
        sa.Column("budget_policy_version", sa.String(length=64), nullable=False),
        sa.Column("estimated_tokens", sa.Integer(), nullable=False),
        sa.Column("source_count", sa.Integer(), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("draft_revision >= 1", name="ck_context_snapshots_revision_positive"),
        sa.CheckConstraint("attempt >= 1 AND attempt <= 2", name="ck_context_snapshots_attempt"),
        sa.CheckConstraint("estimated_tokens >= 0", name="ck_context_snapshots_tokens"),
        sa.CheckConstraint("source_count >= 0", name="ck_context_snapshots_sources"),
        sa.CheckConstraint(
            "status IN ('ready','budget_exceeded','stale_input','conflicting_input')",
            name="ck_context_snapshots_status",
        ),
        sa.ForeignKeyConstraint(["project_id"], ["projects.project_id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("context_snapshot_id"),
        sa.UniqueConstraint("context_hash"),
    )
    op.create_index(
        op.f("ix_context_snapshots_project_id"),
        "context_snapshots",
        ["project_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_context_snapshots_draft_id"),
        "context_snapshots",
        ["draft_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_context_snapshots_draft_id"), table_name="context_snapshots")
    op.drop_index(op.f("ix_context_snapshots_project_id"), table_name="context_snapshots")
    op.drop_table("context_snapshots")
    op.drop_column("requirement_draft_revisions", "context_snapshot_ids")
