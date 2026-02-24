"""add user role for granular permissions

Revision ID: 1c2d3e4f5a6b
Revises: 7d522e70629f
Create Date: 2026-02-24 12:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '1c2d3e4f5a6b'
down_revision = '7d522e70629f'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('user', schema=None) as batch_op:
        batch_op.add_column(sa.Column('role', sa.String(length=30), nullable=False, server_default='user'))

    op.execute("UPDATE user SET role = 'admin' WHERE is_admin = 1")


def downgrade():
    with op.batch_alter_table('user', schema=None) as batch_op:
        batch_op.drop_column('role')
