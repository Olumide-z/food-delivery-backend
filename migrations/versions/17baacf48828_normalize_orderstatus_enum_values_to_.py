"""normalize_orderstatus_enum_values_to_lowercase

Revision ID: 17baacf48828
Revises: 34240084bfc9
Create Date: 2026-09-19 12:31:48.852958

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '17baacf48828'
down_revision: Union[str, Sequence[str], None] = '34240084bfc9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # Rename uppercase enum labels to lowercase to match Python enum values
    op.execute("ALTER TYPE orderstatus RENAME VALUE 'PENDING_PAYMENT' TO 'pending_payment'")
    op.execute("ALTER TYPE orderstatus RENAME VALUE 'PAID' TO 'paid'")
    op.execute("ALTER TYPE orderstatus RENAME VALUE 'PREPARING' TO 'preparing'")
    op.execute("ALTER TYPE orderstatus RENAME VALUE 'READY_FOR_PICKUP' TO 'ready_for_pickup'")
    op.execute("ALTER TYPE orderstatus RENAME VALUE 'OUT_FOR_DELIVERY' TO 'out_for_delivery'")
    op.execute("ALTER TYPE orderstatus RENAME VALUE 'DELIVERED' TO 'delivered'")
    op.execute("ALTER TYPE orderstatus RENAME VALUE 'CANCELLED' TO 'cancelled'")
    op.execute("ALTER TYPE paymentstatus RENAME VALUE 'PENDING' TO 'pending'")
    op.execute("ALTER TYPE paymentstatus RENAME VALUE 'SUCCESS' TO 'success'")
    op.execute("ALTER TYPE paymentstatus RENAME VALUE 'FAILED' TO 'failed'")
    op.execute("ALTER TYPE paymentstatus RENAME VALUE 'REFUNDED' TO 'refunded'")


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("ALTER TYPE orderstatus RENAME VALUE 'pending_payment' TO 'PENDING_PAYMENT'")
    op.execute("ALTER TYPE orderstatus RENAME VALUE 'paid' TO 'PAID'")
    op.execute("ALTER TYPE orderstatus RENAME VALUE 'preparing' TO 'PREPARING'")
    op.execute("ALTER TYPE orderstatus RENAME VALUE 'ready_for_pickup' TO 'READY_FOR_PICKUP'")
    op.execute("ALTER TYPE orderstatus RENAME VALUE 'out_for_delivery' TO 'OUT_FOR_DELIVERY'")
    op.execute("ALTER TYPE orderstatus RENAME VALUE 'delivered' TO 'DELIVERED'")
    op.execute("ALTER TYPE orderstatus RENAME VALUE 'cancelled' TO 'CANCELLED'")
    op.execute("ALTER TYPE paymentstatus RENAME VALUE 'pending' TO 'PENDING'")
    op.execute("ALTER TYPE paymentstatus RENAME VALUE 'success' TO 'SUCCESS'")
    op.execute("ALTER TYPE paymentstatus RENAME VALUE 'failed' TO 'FAILED'")
    op.execute("ALTER TYPE paymentstatus RENAME VALUE 'refunded' TO 'REFUNDED'")
