"""circle invite codes and member roles

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-08

Hand-written:
- circles.invite_code: added nullable, backfilled with a fresh token per row, then made NOT NULL + unique.
- circle_members.role: "owner" | "member". Backfilled "owner" for each circle's creator. A circle whose
  creator is gone (created_by NULL or no longer a member) promotes its earliest member so every circle
  keeps exactly one owner.
Batch mode so SQLite can apply the NOT NULL / unique changes (it rebuilds the table).
"""
import secrets
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: Union[str, Sequence[str], None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()

    # ---- invite codes
    with op.batch_alter_table("circles", schema=None) as batch_op:
        batch_op.add_column(sa.Column("invite_code", sa.String(length=32), nullable=True))

    circles = sa.table("circles", sa.column("id", sa.Integer), sa.column("invite_code", sa.String), sa.column("created_by", sa.Integer))
    used: set[str] = set()
    for (cid,) in conn.execute(sa.select(circles.c.id)).all():
        code = secrets.token_urlsafe(8)
        while code in used:  # astronomically unlikely, but the constraint below would reject it
            code = secrets.token_urlsafe(8)
        used.add(code)
        conn.execute(circles.update().where(circles.c.id == cid).values(invite_code=code))

    with op.batch_alter_table("circles", schema=None) as batch_op:
        batch_op.alter_column("invite_code", existing_type=sa.String(length=32), nullable=False)
        batch_op.create_unique_constraint("uq_circles_invite_code", ["invite_code"])

    # ---- member roles
    with op.batch_alter_table("circle_members", schema=None) as batch_op:
        batch_op.add_column(sa.Column("role", sa.String(length=10), nullable=False, server_default="member"))

    members = sa.table(
        "circle_members",
        sa.column("id", sa.Integer),
        sa.column("circle_id", sa.Integer),
        sa.column("user_id", sa.Integer),
        sa.column("joined_at", sa.DateTime),
        sa.column("role", sa.String),
    )
    for cid, creator in conn.execute(sa.select(circles.c.id, circles.c.created_by)).all():
        owner_row = None
        if creator is not None:
            owner_row = conn.execute(
                sa.select(members.c.id).where(members.c.circle_id == cid, members.c.user_id == creator)
            ).scalar()
        if owner_row is None:
            owner_row = conn.execute(
                sa.select(members.c.id).where(members.c.circle_id == cid).order_by(members.c.joined_at, members.c.id).limit(1)
            ).scalar()
        if owner_row is not None:
            conn.execute(members.update().where(members.c.id == owner_row).values(role="owner"))


def downgrade() -> None:
    with op.batch_alter_table("circle_members", schema=None) as batch_op:
        batch_op.drop_column("role")
    with op.batch_alter_table("circles", schema=None) as batch_op:
        batch_op.drop_constraint("uq_circles_invite_code", type_="unique")
        batch_op.drop_column("invite_code")
