"""Add telegram handle and delivery channel for subscriptions

Revision ID: c4b92e11d001
Revises: a12f4c88d91b
Create Date: 2026-02-12 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'c4b92e11d001'
down_revision = 'a12f4c88d91b'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('user', schema=None) as batch_op:
        batch_op.add_column(sa.Column('telegram_handle', sa.String(length=120), nullable=True))

    with op.batch_alter_table('reading_schedule', schema=None) as batch_op:
        batch_op.add_column(sa.Column('delivery_channel', sa.String(length=20), nullable=False, server_default='email'))


def downgrade():
    with op.batch_alter_table('reading_schedule', schema=None) as batch_op:
        batch_op.drop_column('delivery_channel')

    with op.batch_alter_table('user', schema=None) as batch_op:
        batch_op.drop_column('telegram_handle')
