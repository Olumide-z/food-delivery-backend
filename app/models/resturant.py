from __future__ import annotations
from typing import Optional
from sqlalchemy.orm import mapped_column, Mapped, relationship
from sqlalchemy import Integer, String, Float, ForeignKey, Boolean, UniqueConstraint, DateTime, CheckConstraint, Index
from app.core.database import Base
from app.models.user import User
from app.models.order import Order
from datetime import datetime, UTC


class Resturants(Base):
    __tablename__ = "resturants"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    description: Mapped[str] = mapped_column(String(255), nullable=False)
    address: Mapped[str] = mapped_column(String(255), nullable=False)
    phone: Mapped[str] = mapped_column(String(20), nullable=False)
    email: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    website: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    logo: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    cover_image: Mapped[Optional[str]] = mapped_column(
        String(255), nullable=True)
    rating: Mapped[float] = mapped_column(Float, default=0.0, index=True)
    review_count: Mapped[int] = mapped_column(Integer, default=0)

    # Relationship back to the owning vendor user
    # lets you do resturant.user
    user: Mapped[User] = relationship(
        "User", back_populates="resturants", lazy="selectin"
    )

    # lets you do resturant.menus, menus belonging to the resturant
    menus: Mapped[list[Menu]] = relationship(
        "Menu", back_populates="restaurant", cascade="all, delete-orphan"
    )

    reviews: Mapped[list[Reviews]] = relationship(
        "Reviews", back_populates="restaurant", cascade="all, delete-orphan"
    )


class Menu(Base):
    __tablename__ = 'menus'
    __table_args__ = (
        UniqueConstraint('restaurant_id', 'name',
                         name='uq_restaurant_menu_name'),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    restaurant_id: Mapped[int] = mapped_column(
        ForeignKey("resturants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    description: Mapped[Optional[str]] = mapped_column(
        String(500), nullable=True)
    price: Mapped[int] = mapped_column(
        Integer, default=0)  # Minor units (e.g. cents/kobo)
    image_url: Mapped[Optional[str]] = mapped_column(
        String(255), nullable=True)
    category: Mapped[Optional[str]] = mapped_column(
        String(50), nullable=True, index=True)
    is_available: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    is_vegetarian: Mapped[bool] = mapped_column(Boolean, default=False)
    is_spicy: Mapped[bool] = mapped_column(Boolean, default=False)
    prep_time_minutes: Mapped[Optional[int]] = mapped_column(
        Integer, nullable=True)

    # relationships
    restaurant: Mapped[Resturants] = relationship(
        "Resturants", back_populates="menus", lazy="selectin"
    )

    menus_options: Mapped[list[MenuOptionGroup]] = relationship(
        "MenuOptionGroup", back_populates="menu", cascade="all, delete-orphan"
    )


class MenuOptionGroup(Base):
    __tablename__ = "menu_options_group"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    menu_id: Mapped[int] = mapped_column(
        ForeignKey("menus.id", ondelete="CASCADE"), nullable=False, index=True
    )

    # e.g. "Choose Toppings", "Choice of Drink"
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    is_required: Mapped[bool] = mapped_column(Boolean, default=False)
    min_selectable: Mapped[int] = mapped_column(
        Integer, default=0)  # 0 for optional, 1 for required
    # 1 for single choice (radio), >1 for multi-select (checkboxes)
    max_selectable: Mapped[int] = mapped_column(Integer, default=1)

    # relationships
    menu: Mapped[Menu] = relationship(
        "Menu", back_populates="menus_options", lazy="selectin"
    )

    options: Mapped[list[MenuOptionItem]] = relationship(
        "MenuOptionItem", back_populates="menu_group", cascade="all, delete-orphan"
    )


class MenuOptionItem(Base):
    __tablename__ = "menu_option_item"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    menu_group_id: Mapped[int] = mapped_column(
        ForeignKey("menu_options_group.id", ondelete="CASCADE"), nullable=False, index=True
    )

    # e.g. "Extra Cheese", "Large Sprite"
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    additional_price: Mapped[int] = mapped_column(
        Integer, default=0)  # e.g. 150 (for $1.50)
    is_available: Mapped[bool] = mapped_column(Boolean, default=True)

    # relationship
    menu_group: Mapped[MenuOptionGroup] = relationship(
        "MenuOptionGroup", back_populates="options")


class Reviews(Base):
    __tablename__ = 'reviews'
    __table_args__ = (
        UniqueConstraint('order_id', name='uq_order_review'),
        CheckConstraint('rating >= 1 AND rating <= 5',
                        name='check_restaurant_rating_range'),
        CheckConstraint('rider_rating IS NULL OR (rider_rating >= 1 AND rider_rating <= 5)',
                        name='check_rider_rating_range'),
        # Composite: fetch latest reviews for a restaurant efficiently
        Index('idx_reviews_restaurant_created', 'restaurant_id', 'created_at'),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    order_id: Mapped[int] = mapped_column(
        ForeignKey("orders.id", ondelete="CASCADE"), nullable=False)
    restaurant_id: Mapped[int] = mapped_column(
        ForeignKey("resturants.id", ondelete="CASCADE"), nullable=False, index=True)
    rider_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    rating: Mapped[int] = mapped_column(
        Integer, nullable=False)  # 1 to 5 stars for restaurant
    comment: Mapped[Optional[str]] = mapped_column(
        String(500), nullable=True)  # Optional text review
    rider_rating: Mapped[Optional[int]] = mapped_column(
        Integer, nullable=True)  # 1 to 5 stars for rider
    rider_comment: Mapped[Optional[str]] = mapped_column(
        String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC))
    # relationships
    user: Mapped[User] = relationship(
        "User", foreign_keys=[user_id], lazy="selectin"
    )

    rider: Mapped[Optional[User]] = relationship(
        "User", foreign_keys=[rider_id], lazy="selectin"
    )

    order: Mapped[Order] = relationship(
        "Order", lazy='selectin'
    )

    restaurant: Mapped[Resturants] = relationship(
        "Resturants", back_populates="reviews", lazy="selectin"
    )
