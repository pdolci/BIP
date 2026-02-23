"""add book cover binary storage

Revision ID: d9c1a4e5f901
Revises: b2f9c8a1e4d7
Create Date: 2026-02-22
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'd9c1a4e5f901'
down_revision = 'b2f9c8a1e4d7'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('book', schema=None) as batch_op:
        batch_op.add_column(sa.Column('cover_image_data', sa.LargeBinary(), nullable=True))
        batch_op.add_column(sa.Column('cover_image_mime', sa.String(length=120), nullable=True))


def downgrade():
    with op.batch_alter_table('book', schema=None) as batch_op:
        batch_op.drop_column('cover_image_mime')
        batch_op.drop_column('cover_image_data')
