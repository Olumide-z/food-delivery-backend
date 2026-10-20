"""add_rider_going_to_vendor_to_orderstatus_enum

Revision ID: 34240084bfc9
Revises: 0e34418f3c03
Create Date: 2026-09-19 12:25:52.545567

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '34240084bfc9'
down_revision: Union[str, Sequence[str], None] = '0e34418f3c03'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.execute("ALTER TYPE orderstatus ADD VALUE IF NOT EXISTS 'rider_going_to_vendor'")


def downgrade() -> None:
    """Downgrade schema."""
    pass
