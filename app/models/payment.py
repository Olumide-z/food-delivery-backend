from __future__ import annotations
from enum import Enum
from typing import Optional
from app.models.resturant import Resturants
from app.models.user import User
from app.models.order import Order
from sqlalchemy import Integer, ForeignKey, String, Enum as SQLEnum, DateTime, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.core.database import Base
from datetime import datetime, UTC


class TransactionType(str, Enum):
    CREDIT = "credit"
    DEBIT = "debit"


class TransactionStatus(str, Enum):
    PENDING = "pending"
    COMPLETED = "completed"
    FAILED = "failed"


class VendorBanks(Base):
    __tablename__ = 'vendor_banks'

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    restaurant_id: Mapped[int] = mapped_column(
        ForeignKey("resturants.id", ondelete="CASCADE"), nullable=False, index=True)

    bank_name: Mapped[str] = mapped_column(String(120), nullable=False)
    account_number: Mapped[str] = mapped_column(String(20), nullable=False)
    account_name: Mapped[str] = mapped_column(String(120), nullable=False)

    user: Mapped[User] = relationship(
        "User", foreign_keys=[user_id], lazy="selectin"
    )
    restaurant: Mapped[Resturants] = relationship(
        "Resturants", lazy="selectin"
    )


class Wallet(Base):
    __tablename__ = "wallets"
    __table_args__ = (
        # Each user can only have one wallet
        UniqueConstraint("user_id", name="uq_wallet_user_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)

    balance: Mapped[int] = mapped_column(
        Integer, default=0)  # In kobo/minor units

    user: Mapped[User] = relationship(
        "User", foreign_keys=[user_id], lazy="selectin"
    )
    wallet_transactions: Mapped[list[WalletTransaction]] = relationship(
        "WalletTransaction", back_populates="wallet", cascade="all, delete-orphan", passive_deletes=True
    )


class WalletTransaction(Base):
    __tablename__ = "wallet_transactions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    wallet_id: Mapped[int] = mapped_column(
        ForeignKey("wallets.id", ondelete="CASCADE"), nullable=False, index=True)
    order_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("orders.id", ondelete="SET NULL"), nullable=True, index=True)

    amount: Mapped[int] = mapped_column(Integer, nullable=False)
    type: Mapped[TransactionType] = mapped_column(
        SQLEnum(TransactionType, values_callable=lambda x: [
                e.value for e in x]),
        # ✅ no bad default — caller must pass CREDIT or DEBIT explicitly
        nullable=False, index=True)

    status: Mapped[TransactionStatus] = mapped_column(
        SQLEnum(TransactionStatus, values_callable=lambda x: [
                e.value for e in x]),
        default=TransactionStatus.PENDING, nullable=False, index=True)

    description: Mapped[Optional[str]] = mapped_column(
        String(255), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC))

    wallet: Mapped[Wallet] = relationship(
        "Wallet", foreign_keys=[wallet_id], lazy="selectin", back_populates="wallet_transactions"
    )
    order: Mapped[Optional[Order]] = relationship(
        "Order", foreign_keys=[order_id], lazy="selectin"
    )
