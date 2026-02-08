"""Add delivery events table for dashboard history

Revision ID: 3a5f5c2d9b11
Revises: 9f4d2f6a1c3b
Create Date: 2026-02-08 00:30:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '3a5f5c2d9b11'
down_revision = '9f4d2f6a1c3b'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'delivery_event',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('schedule_id', sa.Integer(), nullable=False),
        sa.Column('event_type', sa.String(length=20), nullable=False, server_default='sent'),
        sa.Column('words_count', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('start_word_index', sa.Integer(), nullable=True),
        sa.Column('end_word_index', sa.Integer(), nullable=True),
        sa.Column('note', sa.String(length=255), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.func.current_timestamp()),
        sa.ForeignKeyConstraint(['schedule_id'], ['reading_schedule.id']),
        sa.PrimaryKeyConstraint('id')
    )


def downgrade():
    op.drop_table('delivery_event')
