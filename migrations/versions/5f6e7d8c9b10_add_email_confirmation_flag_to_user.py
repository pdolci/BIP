"""add email confirmation flag to user

Revision ID: 5f6e7d8c9b10
Revises: f3c9b7a1d2e4
Create Date: 2026-02-24
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "5f6e7d8c9b10"
down_revision = "f3c9b7a1d2e4"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("user", schema=None) as batch_op:
        batch_op.add_column(sa.Column("email_confirmed", sa.Boolean(), nullable=False, server_default=sa.false()))

    op.execute("UPDATE user SET email_confirmed = 1")

    with op.batch_alter_table("user", schema=None) as batch_op:
        batch_op.alter_column("email_confirmed", server_default=None)


def downgrade():
    with op.batch_alter_table("user", schema=None) as batch_op:
        batch_op.drop_column("email_confirmed")
