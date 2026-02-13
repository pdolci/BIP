"""Add preferred delivery channel on user profile

Revision ID: f21d6c4e9aa1
Revises: c4b92e11d001
Create Date: 2026-02-13 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'f21d6c4e9aa1'
down_revision = 'c4b92e11d001'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('user', schema=None) as batch_op:
        batch_op.add_column(sa.Column('preferred_delivery_channel', sa.String(length=20), nullable=False, server_default='email'))


def downgrade():
    with op.batch_alter_table('user', schema=None) as batch_op:
        batch_op.drop_column('preferred_delivery_channel')
