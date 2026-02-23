"""add 2mb limit for book cover binary data

Revision ID: e1b2c3d4f5a6
Revises: d9c1a4e5f901
Create Date: 2026-02-23
"""

from alembic import op


# revision identifiers, used by Alembic.
revision = "e1b2c3d4f5a6"
down_revision = "d9c1a4e5f901"
branch_labels = None
depends_on = None


MAX_COVER_SIZE_BYTES = 2 * 1024 * 1024
CHECK_CONSTRAINT_NAME = "ck_book_cover_image_data_max_size"


def upgrade():
    with op.batch_alter_table("book", schema=None) as batch_op:
        batch_op.create_check_constraint(
            CHECK_CONSTRAINT_NAME,
            f"cover_image_data IS NULL OR length(cover_image_data) <= {MAX_COVER_SIZE_BYTES}",
        )


def downgrade():
    with op.batch_alter_table("book", schema=None) as batch_op:
        batch_op.drop_constraint(CHECK_CONSTRAINT_NAME, type_="check")
