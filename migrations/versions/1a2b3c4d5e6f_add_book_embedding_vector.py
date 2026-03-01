"""add book embedding vector

Revision ID: 1a2b3c4d5e6f
Revises: 2c1d4e6f8a9b
Create Date: 2026-03-01 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "1a2b3c4d5e6f"
down_revision = "2c1d4e6f8a9b"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("book", sa.Column("embedding_vector", sa.Text(), nullable=True))


def downgrade():
    op.drop_column("book", "embedding_vector")
