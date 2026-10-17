"""Internal manual orders and immutable order item snapshots."""
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger, CheckConstraint, DateTime, ForeignKeyConstraint, Index, Numeric,
    String, Text, UniqueConstraint, func, text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Order(Base):
    __tablename__ = "orders"
    __table_args__ = (
        ForeignKeyConstraint(["merchant_id", "shop_id"], ["shops.merchant_id", "shops.id"],
                             name="fk_orders_merchant_shop", ondelete="RESTRICT", onupdate="RESTRICT"),
        ForeignKeyConstraint(["created_by_user_id"], ["users.id"],
                             name="fk_orders_created_by", ondelete="RESTRICT", onupdate="RESTRICT"),
        UniqueConstraint("shop_id", "request_key", name="uq_orders_shop_request_key"),
        UniqueConstraint("merchant_id", "id", name="uq_orders_merchant_id"),
        CheckConstraint("status IN ('PENDING', 'COMPLETED', 'CANCELLED')", name="status"),
        CheckConstraint("total_amount >= 0", name="total_amount_nonnegative"),
        Index("ix_orders_merchant_created_id", "merchant_id", "created_at", "id"),
        Index("ix_orders_shop_created_id", "shop_id", "created_at", "id"),
        Index("ix_orders_shop_status_created_id", "shop_id", "status", "created_at", "id"),
        {"mysql_engine": "InnoDB", "mysql_charset": "utf8mb4", "mysql_collate": "utf8mb4_unicode_ci"},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    merchant_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    shop_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    created_by_user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    status: Mapped[str] = mapped_column(String(16, collation="utf8mb4_bin"), nullable=False, server_default="PENDING")
    total_amount: Mapped[Decimal] = mapped_column(Numeric(precision=38, scale=2), nullable=False)
    request_key: Mapped[str] = mapped_column(String(128, collation="utf8mb4_bin"), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64, collation="ascii_bin"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP"))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime)
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime)


class OrderItem(Base):
    __tablename__ = "order_items"
    __table_args__ = (
        ForeignKeyConstraint(["merchant_id", "order_id"], ["orders.merchant_id", "orders.id"],
                             name="fk_order_items_merchant_order", ondelete="RESTRICT", onupdate="RESTRICT"),
        ForeignKeyConstraint(["merchant_id", "sku_id"], ["skus.merchant_id", "skus.id"],
                             name="fk_order_items_merchant_sku", ondelete="RESTRICT", onupdate="RESTRICT"),
        UniqueConstraint("order_id", "sku_id", name="uq_order_items_order_sku"),
        UniqueConstraint("id", "merchant_id", "sku_id", name="uq_order_items_id_merchant_sku"),
        CheckConstraint("quantity > 0", name="quantity_positive"),
        CheckConstraint("unit_price >= 0 AND subtotal >= 0", name="amounts_nonnegative"),
        CheckConstraint("CAST(unit_price AS DECIMAL(38,2)) * CAST(quantity AS DECIMAL(20,0)) = subtotal", name="subtotal_matches"),
        Index("ix_order_items_merchant_sku", "merchant_id", "sku_id"),
        {"mysql_engine": "InnoDB", "mysql_charset": "utf8mb4", "mysql_collate": "utf8mb4_unicode_ci"},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    order_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    merchant_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    sku_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    quantity: Mapped[int] = mapped_column(BigInteger, nullable=False)
    unit_price: Mapped[Decimal] = mapped_column(Numeric(precision=12, scale=2), nullable=False)
    subtotal: Mapped[Decimal] = mapped_column(Numeric(precision=32, scale=2), nullable=False)
    sku_code_snapshot: Mapped[str] = mapped_column(String(100), nullable=False)
    spu_name_snapshot: Mapped[str] = mapped_column(String(255), nullable=False)
    specification_snapshot: Mapped[str] = mapped_column(Text, nullable=False)

