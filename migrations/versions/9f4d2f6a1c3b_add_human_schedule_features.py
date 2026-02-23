"""Add human scheduling and travel mode fields

Revision ID: 9f4d2f6a1c3b
Revises: eb718335cea1
Create Date: 2026-02-08 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '9f4d2f6a1c3b'
down_revision = 'eb718335cea1'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('reading_schedule', schema=None) as batch_op:
        batch_op.add_column(sa.Column('frequency_mode', sa.String(length=20), nullable=False, server_default='interval'))
        batch_op.add_column(sa.Column('frequency_weekdays', sa.String(length=20), nullable=True))
        batch_op.add_column(sa.Column('delivery_time', sa.Time(), nullable=True))
        batch_op.add_column(sa.Column('travel_pause_until', sa.DateTime(), nullable=True))


def downgrade():
    with op.batch_alter_table('reading_schedule', schema=None) as batch_op:
        batch_op.drop_column('travel_pause_until')
        batch_op.drop_column('delivery_time')
        batch_op.drop_column('frequency_weekdays')
        batch_op.drop_column('frequency_mode')
