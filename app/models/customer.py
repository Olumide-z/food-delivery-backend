from __future__ import annotations

from sqlalchemy import func, Index, false
from enum import Enum
from sqlalchemy import Integer, String, Boolean, ForeignKey, DateTime, UniqueConstraint, Enum as SQLEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.core.database import Base
from datetime import datetime, UTC
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.models.user import User
    from app.models.resturant import Resturants


class DeliveryAddress(Base):
    __tablename__ = "delivery_addresses"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    address: Mapped[str] = mapped_column(String(250), nullable=False)
    is_primary: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        server_default=false(),
        nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())

    user: Mapped[User] = relationship(
        "User", back_populates="delivery_addresses", lazy="selectin"
    )
    __table_args__ = (
        Index(
            "uq_delivery_addresses_primary_per_user",
            user_id,
            unique=True,
            postgresql_where=is_primary.is_(True),
            sqlite_where=is_primary.is_(True),  # SQLite 3.8.9+ supports partial indexes
        ),
    )


class Bookmark(Base):
    __tablename__ = "bookmarked_restaurants"
    __table_args__ = (
        UniqueConstraint("user_id", "restaurant_id",
                         name="uq_user_restaurant_bookmark"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    restaurant_id: Mapped[int] = mapped_column(
        ForeignKey("resturants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )

    # Relationships
    user: Mapped[User] = relationship("User", lazy='selectin')
    restaurant: Mapped[Resturants] = relationship(
        "Resturants", lazy="selectin")
