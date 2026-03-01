"""Remove redundant schedule frequency columns.

Revision ID: 4e7d1b2c9aef
Revises: 1a2b3c4d5e6f
Create Date: 2026-03-01 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "4e7d1b2c9aef"
down_revision = "1a2b3c4d5e6f"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        """
        UPDATE reading_schedule
        SET frequency_type = COALESCE(NULLIF(frequency_type, ''), NULLIF(frequency_mode, ''), 'every_n_days')
        """
    )
    op.execute(
        """
        UPDATE reading_schedule
        SET weekdays = COALESCE(NULLIF(weekdays, ''), NULLIF(frequency_weekdays, ''))
        """
    )

    with op.batch_alter_table("reading_schedule", schema=None) as batch_op:
        batch_op.drop_column("frequency_weekdays")
        batch_op.drop_column("frequency_mode")


def downgrade():
    with op.batch_alter_table("reading_schedule", schema=None) as batch_op:
        batch_op.add_column(sa.Column("frequency_mode", sa.String(length=20), nullable=False, server_default="interval"))
        batch_op.add_column(sa.Column("frequency_weekdays", sa.String(length=20), nullable=True))

    op.execute(
        """
        UPDATE reading_schedule
        SET frequency_mode = COALESCE(NULLIF(frequency_type, ''), 'every_n_days')
        """
    )
    op.execute(
        """
        UPDATE reading_schedule
        SET frequency_weekdays = weekdays
        """
    )
