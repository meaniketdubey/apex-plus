"""circle challenges

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-08

New table only; scores are computed from session_sets at read time, so there is nothing to backfill.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: Union[str, Sequence[str], None] = "0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "circle_challenges",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("circle_id", sa.Integer(), nullable=False),
        sa.Column("created_by", sa.Integer(), nullable=True),
        sa.Column("title", sa.String(length=80), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("target", sa.Float(), nullable=True),
        sa.Column("starts_on", sa.Date(), nullable=False),
        sa.Column("ends_on", sa.Date(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["circle_id"], ["circles.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("circle_challenges", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_circle_challenges_circle_id"), ["circle_id"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("circle_challenges", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_circle_challenges_circle_id"))
    op.drop_table("circle_challenges")
