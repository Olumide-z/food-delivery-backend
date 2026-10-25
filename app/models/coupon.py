"""
Coupon model — vendor-created discount codes applied at checkout.
"""
from __future__ import annotations
from enum import Enum
from typing import Optional, TYPE_CHECKING
from sqlalchemy import Integer, String, Boolean, ForeignKey, Enum as SQLEnum, DateTime, UniqueConstraint
from sqlalchemy.orm import mapped_column, Mapped, relationship
from app.core.database import Base
from datetime import datetime, UTC

if TYPE_CHECKING:
    from app.models.resturant import Resturants


class DiscountType(str, Enum):
    PERCENTAGE = "percentage"   # e.g. 10 = 10% off subtotal
    FLAT = "flat"               # e.g. 500 = ₦500 off subtotal


class Coupon(Base):
    __tablename__ = "coupons"
    __table_args__ = (
        # A coupon code must be unique per restaurant
        UniqueConstraint("restaurant_id", "code", name="uq_coupon_restaurant_code"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    restaurant_id: Mapped[int] = mapped_column(
        ForeignKey("resturants.id", ondelete="CASCADE"), nullable=False, index=True
    )

    code: Mapped[str] = mapped_column(String(50), nullable=False, index=True)

    discount_type: Mapped[DiscountType] = mapped_column(
        SQLEnum(DiscountType, values_callable=lambda x: [e.value for e in x]),
        nullable=False
    )
    discount_value: Mapped[int] = mapped_column(
        Integer, nullable=False
    )  # Percentage (0-100) or flat amount in kobo

    min_order_amount: Mapped[int] = mapped_column(
        Integer, default=0
    )  # Minimum subtotal required to apply this coupon (in kobo). 0 = no minimum.

    max_uses: Mapped[Optional[int]] = mapped_column(
        Integer, nullable=True
    )  # Max times it can be used. NULL = unlimited.

    used_count: Mapped[int] = mapped_column(Integer, default=0)

    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)

    expires_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )

    # Relationships
    restaurant: Mapped[Resturants] = relationship("Resturants", lazy="selectin")
