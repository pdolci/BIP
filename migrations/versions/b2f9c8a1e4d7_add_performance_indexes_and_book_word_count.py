"""add performance indexes and book word count

Revision ID: b2f9c8a1e4d7
Revises: f21d6c4e9aa1
Create Date: 2026-02-14 08:10:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'b2f9c8a1e4d7'
down_revision = 'f21d6c4e9aa1'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('book', schema=None) as batch_op:
        batch_op.add_column(sa.Column('word_count', sa.Integer(), nullable=False, server_default='0'))
        batch_op.create_index('ix_book_is_active_title', ['is_active', 'title'], unique=False)

    with op.batch_alter_table('reading_schedule', schema=None) as batch_op:
        batch_op.create_index('ix_reading_schedule_due', ['next_send_date', 'is_paused', 'travel_pause_until'], unique=False)
        batch_op.create_index('ix_reading_schedule_user_active', ['user_id', 'is_paused'], unique=False)

    with op.batch_alter_table('delivery_event', schema=None) as batch_op:
        batch_op.create_index('ix_delivery_event_schedule_created', ['schedule_id', 'created_at'], unique=False)


def downgrade():
    with op.batch_alter_table('delivery_event', schema=None) as batch_op:
        batch_op.drop_index('ix_delivery_event_schedule_created')

    with op.batch_alter_table('reading_schedule', schema=None) as batch_op:
        batch_op.drop_index('ix_reading_schedule_user_active')
        batch_op.drop_index('ix_reading_schedule_due')

    with op.batch_alter_table('book', schema=None) as batch_op:
        batch_op.drop_index('ix_book_is_active_title')
        batch_op.drop_column('word_count')
