"""timezone and set client id

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-07 18:39:07.856508

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0002'
down_revision: Union[str, Sequence[str], None] = '0001'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table('profiles', schema=None) as batch_op:
        batch_op.add_column(sa.Column('timezone', sa.String(length=64), nullable=False, server_default='UTC'))

    with op.batch_alter_table('session_sets', schema=None) as batch_op:
        batch_op.add_column(sa.Column('client_id', sa.String(length=64), nullable=True))
        batch_op.create_unique_constraint('uq_session_sets_session_client', ['session_id', 'client_id'])



def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('session_sets', schema=None) as batch_op:
        batch_op.drop_constraint('uq_session_sets_session_client', type_='unique')
        batch_op.drop_column('client_id')

    with op.batch_alter_table('profiles', schema=None) as batch_op:
        batch_op.drop_column('timezone')

