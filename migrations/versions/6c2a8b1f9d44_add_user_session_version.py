"""Add server-side user session version

Revision ID: 6c2a8b1f9d44
Revises: 4e7d1b2c9aef
Create Date: 2026-03-01 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '6c2a8b1f9d44'
down_revision = '4e7d1b2c9aef'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('user', schema=None) as batch_op:
        batch_op.add_column(sa.Column('session_version', sa.Integer(), nullable=False, server_default='0'))


def downgrade():
    with op.batch_alter_table('user', schema=None) as batch_op:
        batch_op.drop_column('session_version')
