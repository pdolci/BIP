"""Add metadata fields on book for advanced search filters

Revision ID: a12f4c88d91b
Revises: 3a5f5c2d9b11
Create Date: 2026-02-10 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'a12f4c88d91b'
down_revision = '3a5f5c2d9b11'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('book', schema=None) as batch_op:
        batch_op.add_column(sa.Column('author', sa.String(length=255), nullable=True))
        batch_op.add_column(sa.Column('publication_year', sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column('genre', sa.String(length=120), nullable=True))
        batch_op.add_column(sa.Column('tags', sa.String(length=500), nullable=True))
        batch_op.add_column(sa.Column('language', sa.String(length=80), nullable=True))
        batch_op.add_column(sa.Column('estimated_reading_hours', sa.Float(), nullable=True))
        batch_op.add_column(sa.Column('cover_image', sa.String(length=500), nullable=True))


def downgrade():
    with op.batch_alter_table('book', schema=None) as batch_op:
        batch_op.drop_column('cover_image')
        batch_op.drop_column('estimated_reading_hours')
        batch_op.drop_column('language')
        batch_op.drop_column('tags')
        batch_op.drop_column('genre')
        batch_op.drop_column('publication_year')
        batch_op.drop_column('author')
