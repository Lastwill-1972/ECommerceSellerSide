"""Merchant warehouses and explicit shop warehouse access grants."""
from datetime import datetime

from sqlalchemy import (
    BigInteger, CheckConstraint, Computed, DateTime, ForeignKey, ForeignKeyConstraint,
    Index, String, UniqueConstraint, func, text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Warehouse(Base):
    __tablename__ = "warehouses"
    __table_args__ = (
        UniqueConstraint("merchant_id", "id", name="uq_warehouses_merchant_id_id"),
        {"mysql_engine": "InnoDB", "mysql_charset": "utf8mb4", "mysql_collate": "utf8mb4_unicode_ci"},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    merchant_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("merchants.id", ondelete="RESTRICT", onupdate="RESTRICT"),
        nullable=False,
        unique=True,
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP"))


class Stock(Base):
    __tablename__ = "stocks"
    __table_args__ = (
        ForeignKeyConstraint(["merchant_id", "warehouse_id"], ["warehouses.merchant_id", "warehouses.id"],
                             name="fk_stocks_merchant_warehouse", ondelete="RESTRICT", onupdate="RESTRICT"),
        ForeignKeyConstraint(["merchant_id", "sku_id"], ["skus.merchant_id", "skus.id"],
                             name="fk_stocks_merchant_sku", ondelete="RESTRICT", onupdate="RESTRICT"),
        UniqueConstraint("warehouse_id", "sku_id", name="uq_stocks_warehouse_sku"),
        UniqueConstraint("merchant_id", "warehouse_id", "sku_id", name="uq_stocks_merchant_warehouse_sku"),
        CheckConstraint("quantity >= 0", name="quantity_nonnegative"),
        Index("ix_stocks_merchant_sku", "merchant_id", "sku_id"),
        {"mysql_engine": "InnoDB", "mysql_charset": "utf8mb4", "mysql_collate": "utf8mb4_unicode_ci"},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    merchant_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    warehouse_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    sku_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    quantity: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default="0")
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP"))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP"),
        onupdate=func.current_timestamp(),
    )


class StockMovement(Base):
    __tablename__ = "stock_movements"
    __table_args__ = (
        ForeignKeyConstraint(["merchant_id", "warehouse_id"], ["warehouses.merchant_id", "warehouses.id"],
                             name="fk_stock_movements_merchant_warehouse", ondelete="RESTRICT", onupdate="RESTRICT"),
        ForeignKeyConstraint(["merchant_id", "sku_id"], ["skus.merchant_id", "skus.id"],
                             name="fk_stock_movements_merchant_sku", ondelete="RESTRICT", onupdate="RESTRICT"),
        ForeignKeyConstraint(["merchant_id", "warehouse_id", "sku_id"],
                             ["stocks.merchant_id", "stocks.warehouse_id", "stocks.sku_id"],
                             name="fk_stock_movements_stock", ondelete="RESTRICT", onupdate="RESTRICT"),
        ForeignKeyConstraint(["actor_user_id"], ["users.id"], name="fk_stock_movements_actor",
                             ondelete="RESTRICT", onupdate="RESTRICT"),
        ForeignKeyConstraint(["order_item_id", "merchant_id", "sku_id"],
                             ["order_items.id", "order_items.merchant_id", "order_items.sku_id"],
                             name="fk_stock_movements_order_item", ondelete="RESTRICT", onupdate="RESTRICT"),
        UniqueConstraint("merchant_id", "idempotency_key", name="uq_stock_movements_merchant_idempotency"),
        UniqueConstraint("order_item_id", "operation_type", name="uq_stock_movements_order_item_operation"),
        CheckConstraint(
            "(operation_type = 'MANUAL_ADJUSTMENT' AND order_item_id IS NULL "
            "AND idempotency_key IS NOT NULL AND request_hash IS NOT NULL) OR "
            "(operation_type = 'ORDER_DEDUCT' AND order_item_id IS NOT NULL "
            "AND idempotency_key IS NULL AND request_hash IS NULL AND quantity_delta < 0) OR "
            "(operation_type = 'ORDER_RESTORE' AND order_item_id IS NOT NULL "
            "AND idempotency_key IS NULL AND request_hash IS NULL AND quantity_delta > 0)", name="movement_origin_consistent"),
        CheckConstraint("operation_type IN ('MANUAL_ADJUSTMENT', 'ORDER_DEDUCT', 'ORDER_RESTORE')",
                        name="operation_type"),
        CheckConstraint("quantity_delta <> 0", name="quantity_delta_nonzero"),
        CheckConstraint("quantity_before >= 0 AND quantity_after >= 0", name="quantities_nonnegative"),
        CheckConstraint(
            "CAST(quantity_before AS DECIMAL(20,0)) + CAST(quantity_delta AS DECIMAL(20,0)) = "
            "CAST(quantity_after AS DECIMAL(20,0))",
            name="delta_matches_quantities",
        ),
        Index("ix_stock_movements_merchant_sku_id", "merchant_id", "sku_id", "id"),
        {"mysql_engine": "InnoDB", "mysql_charset": "utf8mb4", "mysql_collate": "utf8mb4_unicode_ci"},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    merchant_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    warehouse_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    sku_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    actor_user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    operation_type: Mapped[str] = mapped_column(String(32, collation="utf8mb4_bin"), nullable=False)
    order_item_id: Mapped[int | None] = mapped_column(BigInteger)
    quantity_delta: Mapped[int] = mapped_column(BigInteger, nullable=False)
    quantity_before: Mapped[int] = mapped_column(BigInteger, nullable=False)
    quantity_after: Mapped[int] = mapped_column(BigInteger, nullable=False)
    idempotency_key: Mapped[str | None] = mapped_column(String(128, collation="utf8mb4_bin"))
    request_hash: Mapped[str | None] = mapped_column(String(64, collation="ascii_bin"))
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP"))


class WarehouseAccessApplication(Base):
    __tablename__ = "warehouse_access_applications"
    __table_args__ = (
        CheckConstraint("status IN ('PENDING', 'APPROVED', 'REJECTED')", name="status"),
        ForeignKeyConstraint(["shop_id"], ["shops.id"], name="fk_warehouse_access_applications_shop_id_shops",
                             ondelete="RESTRICT", onupdate="RESTRICT"),
        ForeignKeyConstraint(["applicant_user_id"], ["users.id"],
                             name="fk_warehouse_access_applications_applicant_user_id_users",
                             ondelete="RESTRICT", onupdate="RESTRICT"),
        ForeignKeyConstraint(["reviewer_user_id"], ["users.id"],
                             name="fk_warehouse_access_applications_reviewer_user_id_users",
                             ondelete="RESTRICT", onupdate="RESTRICT"),
        UniqueConstraint("pending_shop_id", name="uq_warehouse_access_applications_pending_shop"),
        Index("ix_warehouse_access_applications_status_id", "status", "id"),
        {"mysql_engine": "InnoDB", "mysql_charset": "utf8mb4", "mysql_collate": "utf8mb4_unicode_ci"},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    shop_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    applicant_user_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(16, collation="utf8mb4_bin"), nullable=False, server_default="PENDING")
    reviewer_user_id: Mapped[int | None] = mapped_column(BigInteger)
    rejection_reason: Mapped[str | None] = mapped_column(String(2000))
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP"))
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime)
    pending_shop_id: Mapped[int | None] = mapped_column(
        BigInteger, Computed("CASE WHEN status = 'PENDING' THEN shop_id ELSE NULL END", persisted=True), nullable=True
    )


class WarehouseAccess(Base):
    __tablename__ = "warehouse_accesses"
    __table_args__ = (
        ForeignKeyConstraint(["merchant_id", "shop_id"], ["shops.merchant_id", "shops.id"],
                             name="fk_warehouse_accesses_merchant_shop",
                             ondelete="RESTRICT", onupdate="RESTRICT"),
        ForeignKeyConstraint(["merchant_id", "warehouse_id"], ["warehouses.merchant_id", "warehouses.id"],
                             name="fk_warehouse_accesses_merchant_warehouse",
                             ondelete="RESTRICT", onupdate="RESTRICT"),
        ForeignKeyConstraint(["granted_by_user_id"], ["users.id"],
                             name="fk_warehouse_accesses_granted_by_user_id_users",
                             ondelete="RESTRICT", onupdate="RESTRICT"),
        UniqueConstraint("shop_id", name="uq_warehouse_accesses_shop_id"),
        {"mysql_engine": "InnoDB", "mysql_charset": "utf8mb4", "mysql_collate": "utf8mb4_unicode_ci"},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    shop_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    warehouse_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    merchant_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    granted_by_user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    granted_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP"))
