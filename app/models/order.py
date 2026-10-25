from __future__ import annotations
from enum import Enum
from app.core.database import Base
from sqlalchemy import Integer, String, Boolean, JSON, ForeignKey, Enum as SQLEnum, DateTime, Index, text
from sqlalchemy.orm import mapped_column, Mapped, relationship
from typing import TYPE_CHECKING, Optional
from datetime import datetime, UTC


if TYPE_CHECKING:
    from app.models.user import User
    from app.models.resturant import Resturants, Menu
    from app.models.customer import DeliveryAddress
    from app.models.coupon import Coupon


class OrderStatus(str, Enum):
    PENDING_PAYMENT = "pending_payment"
    PAID = "paid"
    PREPARING = "preparing"
    READY_FOR_PICKUP = "ready_for_pickup"
    RIDER_GOING_TO_VENDOR = "rider_going_to_vendor"
    OUT_FOR_DELIVERY = "out_for_delivery"
    DELIVERED = "delivered"
    CANCELLED = "cancelled"


class PaymentStatus(str, Enum):
    PENDING = "pending"
    SUCCESS = "success"
    FAILED = "failed"
    REFUNDED = "refunded"


class Order(Base):
    __tablename__ = 'orders'
    __table_args__ = (
        # Composite index: vendor analytics queries filter by restaurant + status constantly
        Index('idx_orders_restaurant_status', 'restaurant_id', 'status'),
        # Composite index: customer order history sorted by date
        Index('idx_orders_customer_created', 'customer_id', 'created_at'),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    order_number: Mapped[str] = mapped_column(
        String(32), unique=True, index=True, nullable=False)
    customer_id: Mapped[int] = mapped_column(ForeignKey(
        "users.id", ondelete='CASCADE'), nullable=False, index=True)
    restaurant_id: Mapped[int] = mapped_column(ForeignKey(
        "resturants.id", ondelete="CASCADE"), nullable=False, index=True)
    rider_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    coupon_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("coupons.id", ondelete="SET NULL"), nullable=True, index=True)

    delivery_address_id: Mapped[int] = mapped_column(
        ForeignKey("delivery_addresses.id"), nullable=False)
    status: Mapped[OrderStatus] = mapped_column(
        SQLEnum(OrderStatus, values_callable=lambda x: [e.value for e in x]),
        default=OrderStatus.PENDING_PAYMENT, nullable=False, index=True)

    payment_status: Mapped[PaymentStatus] = mapped_column(
        SQLEnum(PaymentStatus, values_callable=lambda x: [e.value for e in x]),
        default=PaymentStatus.PENDING, nullable=False, index=True)
    subtotal: Mapped[int] = mapped_column(
        Integer, nullable=False)  # In minor units (cents/kobo)
    delivery_fee: Mapped[int] = mapped_column(Integer, nullable=False)
    service_fee: Mapped[int] = mapped_column(Integer, default=0)
    discount_amount: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0", nullable=False)
    coupon_code: Mapped[Optional[str]] = mapped_column(
        String(50), nullable=True, index=True)  # Applied coupon code
    # subtotal + delivery_fee + service_fee - discount_amount
    total_amount: Mapped[int] = mapped_column(Integer, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(
        timezone=True), default=lambda: datetime.now(UTC), onupdate=lambda: datetime.now(UTC))

    # Relationships
    customer: Mapped[User] = relationship("User", foreign_keys=[customer_id])
    rider: Mapped[Optional[User]] = relationship(
        "User", foreign_keys=[rider_id])
    restaurant: Mapped[Resturants] = relationship("Resturants")
    delivery_address: Mapped[DeliveryAddress] = relationship("DeliveryAddress")
    items: Mapped[list[OrderItem]] = relationship(
        "OrderItem", back_populates="order", cascade="all, delete-orphan")
    coupon: Mapped[Optional[Coupon]] = relationship(
        "Coupon", foreign_keys=[coupon_id])


class OrderItem(Base):
    __tablename__ = 'order_items'

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    order_id: Mapped[int] = mapped_column(ForeignKey(
        "orders.id", ondelete="CASCADE"), nullable=False, index=True)
    menu_id: Mapped[int] = mapped_column(
        ForeignKey("menus.id"), nullable=False, index=True)

    unit_price: Mapped[int] = mapped_column(
        Integer, nullable=False)  # Price snapshot at time of order
    quantity: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    total_price: Mapped[int] = mapped_column(
        Integer, nullable=False)  # unit_price * quantity
    selected_options: Mapped[Optional[list[int]]] = mapped_column(
        JSON, nullable=True)  # Custom add-on options snapshot

    # Relationships
    order: Mapped[Order] = relationship("Order", back_populates="items")
    menu: Mapped[Menu] = relationship("Menu")


class PaymentTransaction(Base):
    __tablename__ = "payment_transactions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    order_id: Mapped[int] = mapped_column(ForeignKey(
        "orders.id", ondelete="CASCADE"), nullable=False, index=True)
    payment_gateway: Mapped[str] = mapped_column(
        String(50), nullable=False)  # "paystack", "stripe", "flutterwave"
    reference: Mapped[str] = mapped_column(
        # Gateway Transaction Ref
        String(100), unique=True, index=True, nullable=False)
    amount: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(
        # "pending", "success", "failed"
        String(20), default="pending", index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC))
