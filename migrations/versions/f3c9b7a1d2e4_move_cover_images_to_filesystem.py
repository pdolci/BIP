"""move cover images to filesystem and drop binary columns

Revision ID: f3c9b7a1d2e4
Revises: e1b2c3d4f5a6
Create Date: 2026-02-23
"""

from pathlib import Path
import uuid

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "f3c9b7a1d2e4"
down_revision = "e1b2c3d4f5a6"
branch_labels = None
depends_on = None

CHECK_CONSTRAINT_NAME = "ck_book_cover_image_data_max_size"


def _detect_extension(mime_type: str | None) -> str:
    mapping = {
        "image/jpeg": "jpg",
        "image/jpg": "jpg",
        "image/png": "png",
        "image/gif": "gif",
        "image/webp": "webp",
    }
    return mapping.get((mime_type or "").lower(), "jpg")


def upgrade():
    bind = op.get_bind()
    rows = bind.execute(
        sa.text(
            """
            SELECT id, cover_image_data, cover_image_mime
            FROM book
            WHERE cover_image_data IS NOT NULL
              AND (cover_image IS NULL OR cover_image = '')
            """
        )
    ).fetchall()

    cover_folder = Path(__file__).resolve().parents[2] / "uploads" / "covers"
    cover_folder.mkdir(parents=True, exist_ok=True)

    for row in rows:
        ext = _detect_extension(row.cover_image_mime)
        filename = f"{uuid.uuid4().hex}.{ext}"
        (cover_folder / filename).write_bytes(row.cover_image_data)
        bind.execute(
            sa.text("UPDATE book SET cover_image = :cover_image WHERE id = :book_id"),
            {"cover_image": f"covers/{filename}", "book_id": row.id},
        )

    with op.batch_alter_table("book", schema=None) as batch_op:
        batch_op.drop_constraint(CHECK_CONSTRAINT_NAME, type_="check")
        batch_op.drop_column("cover_image_mime")
        batch_op.drop_column("cover_image_data")


def downgrade():
    with op.batch_alter_table("book", schema=None) as batch_op:
        batch_op.add_column(sa.Column("cover_image_data", sa.LargeBinary(), nullable=True))
        batch_op.add_column(sa.Column("cover_image_mime", sa.String(length=120), nullable=True))
        batch_op.create_check_constraint(
            CHECK_CONSTRAINT_NAME,
            "cover_image_data IS NULL OR length(cover_image_data) <= 2097152",
        )
