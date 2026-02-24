"""add content manager role

Revision ID: 2c1d4e6f8a9b
Revises: 7d522e70629f
Create Date: 2026-02-24
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "2c1d4e6f8a9b"
down_revision = "7d522e70629f"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("user", schema=None) as batch_op:
        batch_op.add_column(sa.Column("is_content_manager", sa.Boolean(), nullable=False, server_default=sa.false()))

    with op.batch_alter_table("user", schema=None) as batch_op:
        batch_op.alter_column("is_content_manager", server_default=None)


def downgrade():
    with op.batch_alter_table("user", schema=None) as batch_op:
        batch_op.drop_column("is_content_manager")
