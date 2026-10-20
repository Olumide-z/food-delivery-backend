"""normalize_paymentstatus_enum_to_lowercase

Revision ID: 96ae34c03374
Revises: 17baacf48828
Create Date: 2026-09-19 12:34:31.392726

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '96ae34c03374'
down_revision: Union[str, Sequence[str], None] = '17baacf48828'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # No-op: paymentstatus enum values were already renamed to lowercase
    # in the previous migration (17baacf48828). This migration is a duplicate
    # and is kept only to preserve the Alembic revision chain.
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
