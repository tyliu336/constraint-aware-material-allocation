"""Add versioned requirement drafts and semantic parse revisions.

Revision ID: 20261005_0002
Revises: 20261005_0001
Create Date: 2026-10-05
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261005_0002"
down_revision: str | None = "20261005_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "confirmed_requirement_preferences",
        sa.Column("requirement_id", sa.String(length=128), nullable=False),
        sa.Column("preference_type", sa.String(length=64), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(
            ["requirement_id"], ["requirements.requirement_id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("requirement_id", "preference_type"),
    )
    op.create_table(
        "requirement_drafts",
        sa.Column("draft_id", sa.String(length=128), nullable=False),
        sa.Column("project_id", sa.String(length=128), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("current_revision", sa.Integer(), nullable=False),
        sa.Column("created_by", sa.String(length=128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("confirmed_requirement_id", sa.String(length=128), nullable=True),
        sa.CheckConstraint("current_revision >= 1", name="ck_requirement_drafts_revision_positive"),
        sa.CheckConstraint(
            "status IN ('needs_clarification','ready_for_confirmation','parse_failed','confirmed')",
            name="ck_requirement_drafts_status",
        ),
        sa.ForeignKeyConstraint(
            ["confirmed_requirement_id"], ["requirements.requirement_id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(["project_id"], ["projects.project_id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("draft_id"),
        sa.UniqueConstraint("confirmed_requirement_id"),
    )
    op.create_index(
        op.f("ix_requirement_drafts_project_id"),
        "requirement_drafts",
        ["project_id"],
        unique=False,
    )
    op.create_table(
        "requirement_draft_revisions",
        sa.Column("draft_id", sa.String(length=128), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.Column("source_text", sa.Text(), nullable=False),
        sa.Column("source_text_sha256", sa.String(length=64), nullable=False),
        sa.Column("answers", sa.JSON(), nullable=False),
        sa.Column("prompt_name", sa.String(length=128), nullable=False),
        sa.Column("prompt_version", sa.String(length=64), nullable=False),
        sa.Column("prompt_content_sha256", sa.String(length=64), nullable=False),
        sa.Column("model_id", sa.String(length=200), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "attempt_count >= 1 AND attempt_count <= 2",
            name="ck_requirement_draft_revisions_attempts",
        ),
        sa.CheckConstraint("revision >= 1", name="ck_requirement_draft_revisions_positive"),
        sa.ForeignKeyConstraint(["draft_id"], ["requirement_drafts.draft_id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("draft_id", "revision"),
        sa.UniqueConstraint("idempotency_key"),
    )


def downgrade() -> None:
    op.drop_table("requirement_draft_revisions")
    op.drop_index(op.f("ix_requirement_drafts_project_id"), table_name="requirement_drafts")
    op.drop_table("requirement_drafts")
    op.drop_table("confirmed_requirement_preferences")
