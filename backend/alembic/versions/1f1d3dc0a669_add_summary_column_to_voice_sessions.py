"""add summary column to voice_sessions

Revision ID: 1f1d3dc0a669
Revises: f800e3bc70cb
Create Date: 2026-03-22 17:15:22.029355

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '1f1d3dc0a669'
down_revision: Union[str, Sequence[str], None] = 'f800e3bc70cb'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('voice_sessions',
        sa.Column('summary', postgresql.JSON(astext_type=sa.Text()), nullable=True),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('voice_sessions', 'summary')
