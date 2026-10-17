"""internal manual orders and order-backed stock movements

Revision ID: 20261017_orders
Revises: 20261017_inventory
Create Date: 2026-10-17
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20261017_orders"
down_revision: Union[str, Sequence[str], None] = "20261017_inventory"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "orders",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("merchant_id", sa.BigInteger(), nullable=False),
        sa.Column("shop_id", sa.BigInteger(), nullable=False),
        sa.Column("created_by_user_id", sa.BigInteger(), nullable=False),
        sa.Column("status", sa.String(16, collation="utf8mb4_bin"), server_default="PENDING", nullable=False),
        sa.Column("total_amount", sa.Numeric(38, 2), nullable=False),
        sa.Column("request_key", sa.String(128, collation="utf8mb4_bin"), nullable=False),
        sa.Column("request_hash", sa.String(64, collation="ascii_bin"), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.Column("cancelled_at", sa.DateTime(), nullable=True),
        sa.CheckConstraint("status IN ('PENDING', 'COMPLETED', 'CANCELLED')", name=op.f("ck_orders_status")),
        sa.CheckConstraint("total_amount >= 0", name=op.f("ck_orders_total_amount_nonnegative")),
        sa.ForeignKeyConstraint(["merchant_id", "shop_id"], ["shops.merchant_id", "shops.id"],
                                name="fk_orders_merchant_shop", ondelete="RESTRICT", onupdate="RESTRICT"),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"], name="fk_orders_created_by",
                                ondelete="RESTRICT", onupdate="RESTRICT"),
        sa.PrimaryKeyConstraint("id", name="pk_orders"),
        sa.UniqueConstraint("shop_id", "request_key", name="uq_orders_shop_request_key"),
        sa.UniqueConstraint("merchant_id", "id", name="uq_orders_merchant_id"),
        mysql_charset="utf8mb4", mysql_collate="utf8mb4_unicode_ci", mysql_engine="InnoDB",
    )
    op.create_index("ix_orders_merchant_created_id", "orders", ["merchant_id", "created_at", "id"])
    op.create_index("ix_orders_shop_created_id", "orders", ["shop_id", "created_at", "id"])
    op.create_index("ix_orders_shop_status_created_id", "orders", ["shop_id", "status", "created_at", "id"])

    op.create_table(
        "order_items",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("order_id", sa.BigInteger(), nullable=False),
        sa.Column("merchant_id", sa.BigInteger(), nullable=False),
        sa.Column("sku_id", sa.BigInteger(), nullable=False),
        sa.Column("quantity", sa.BigInteger(), nullable=False),
        sa.Column("unit_price", sa.Numeric(12, 2), nullable=False),
        sa.Column("subtotal", sa.Numeric(32, 2), nullable=False),
        sa.Column("sku_code_snapshot", sa.String(100), nullable=False),
        sa.Column("spu_name_snapshot", sa.String(255), nullable=False),
        sa.Column("specification_snapshot", sa.Text(), nullable=False),
        sa.CheckConstraint("quantity > 0", name=op.f("ck_order_items_quantity_positive")),
        sa.CheckConstraint("unit_price >= 0 AND subtotal >= 0", name=op.f("ck_order_items_amounts_nonnegative")),
        sa.CheckConstraint(
            "CAST(unit_price AS DECIMAL(38,2)) * CAST(quantity AS DECIMAL(20,0)) = subtotal",
            name=op.f("ck_order_items_subtotal_matches")),
        sa.ForeignKeyConstraint(["merchant_id", "order_id"], ["orders.merchant_id", "orders.id"],
                                name="fk_order_items_merchant_order", ondelete="RESTRICT", onupdate="RESTRICT"),
        sa.ForeignKeyConstraint(["merchant_id", "sku_id"], ["skus.merchant_id", "skus.id"],
                                name="fk_order_items_merchant_sku", ondelete="RESTRICT", onupdate="RESTRICT"),
        sa.PrimaryKeyConstraint("id", name="pk_order_items"),
        sa.UniqueConstraint("order_id", "sku_id", name="uq_order_items_order_sku"),
        sa.UniqueConstraint("id", "merchant_id", "sku_id", name="uq_order_items_id_merchant_sku"),
        mysql_charset="utf8mb4", mysql_collate="utf8mb4_unicode_ci", mysql_engine="InnoDB",
    )
    op.create_index("ix_order_items_merchant_sku", "order_items", ["merchant_id", "sku_id"])

    # Preserve and classify all historical rows before relaxing the manual-only schema.
    op.drop_constraint(op.f("ck_stock_movements_operation_type_manual_adjustment"), "stock_movements", type_="check")
    op.alter_column("stock_movements", "idempotency_key", existing_type=sa.String(128, collation="utf8mb4_bin"), nullable=True)
    op.alter_column("stock_movements", "request_hash", existing_type=sa.String(64, collation="ascii_bin"), nullable=True)
    op.add_column("stock_movements", sa.Column("order_item_id", sa.BigInteger(), nullable=True))
    op.create_unique_constraint("uq_stock_movements_order_item_operation", "stock_movements", ["order_item_id", "operation_type"])
    op.create_foreign_key(
        "fk_stock_movements_order_item", "stock_movements", "order_items",
        ["order_item_id", "merchant_id", "sku_id"], ["id", "merchant_id", "sku_id"],
        ondelete="RESTRICT", onupdate="RESTRICT",
    )
    op.create_check_constraint(
        op.f("ck_stock_movements_movement_origin_consistent"), "stock_movements",
        "(operation_type = 'MANUAL_ADJUSTMENT' AND order_item_id IS NULL "
        "AND idempotency_key IS NOT NULL AND request_hash IS NOT NULL) OR "
        "(operation_type = 'ORDER_DEDUCT' AND order_item_id IS NOT NULL "
        "AND idempotency_key IS NULL AND request_hash IS NULL AND quantity_delta < 0) OR "
        "(operation_type = 'ORDER_RESTORE' AND order_item_id IS NOT NULL "
        "AND idempotency_key IS NULL AND request_hash IS NULL AND quantity_delta > 0)",
    )
    op.create_check_constraint(
        op.f("ck_stock_movements_operation_type"), "stock_movements",
        "operation_type IN ('MANUAL_ADJUSTMENT', 'ORDER_DEDUCT', 'ORDER_RESTORE')",
    )


def downgrade() -> None:
    bind = op.get_bind()
    if not op.get_context().as_sql:
        order_count, item_count, movement_count = bind.execute(sa.text(
            "SELECT (SELECT COUNT(*) FROM orders), (SELECT COUNT(*) FROM order_items), "
            "(SELECT COUNT(*) FROM stock_movements WHERE order_item_id IS NOT NULL)"
        )).one()
        if order_count or item_count or movement_count:
            raise RuntimeError(
                "cannot downgrade 20261017_orders while order history or order stock movements exist; "
                "preserve order history and inventory movements"
            )

    # Remove only the extension; historical manual movements and their idempotency
    # data remain intact and regain their original required-column/check semantics.
    op.drop_constraint("fk_stock_movements_order_item", "stock_movements", type_="foreignkey")
    op.drop_constraint("uq_stock_movements_order_item_operation", "stock_movements", type_="unique")
    op.drop_constraint(op.f("ck_stock_movements_movement_origin_consistent"), "stock_movements", type_="check")
    op.drop_constraint(op.f("ck_stock_movements_operation_type"), "stock_movements", type_="check")
    op.drop_column("stock_movements", "order_item_id")
    op.alter_column("stock_movements", "idempotency_key", existing_type=sa.String(128, collation="utf8mb4_bin"), nullable=False)
    op.alter_column("stock_movements", "request_hash", existing_type=sa.String(64, collation="ascii_bin"), nullable=False)
    op.create_check_constraint(
        op.f("ck_stock_movements_operation_type_manual_adjustment"), "stock_movements",
        "operation_type = 'MANUAL_ADJUSTMENT'",
    )
    op.drop_table("order_items")
    op.drop_table("orders")

