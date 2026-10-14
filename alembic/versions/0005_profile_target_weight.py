"""profile target weight

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-08

Nullable column, no backfill: nobody has a goal until they set one.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: Union[str, Sequence[str], None] = "0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("profiles", schema=None) as batch_op:
        batch_op.add_column(sa.Column("target_weight_kg", sa.Float(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("profiles", schema=None) as batch_op:
        batch_op.drop_column("target_weight_kg")
