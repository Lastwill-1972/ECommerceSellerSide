"""Shared merchant stock, immutable movements and SKU tenant key.

Revision ID: 20261017_inventory
Revises: 20261017_warehouse_access
Create Date: 2026-10-17
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20261017_inventory"
down_revision: Union[str, Sequence[str], None] = "20261017_warehouse_access"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    context = op.get_context()
    if not context.as_sql:
        # MySQL DDL autocommits. Validate the historical rows before making any
        # schema changes so an unsafe backfill cannot leave a partial revision.
        bad_rows = bind.execute(sa.text(
            "SELECT s.id, s.spu_id FROM skus AS s "
            "LEFT JOIN spus AS p ON p.id = s.spu_id "
            "LEFT JOIN warehouses AS w ON w.merchant_id = p.merchant_id "
            "WHERE p.id IS NULL OR w.id IS NULL LIMIT 10"
        )).all()
        if bad_rows:
            raise RuntimeError(
                "inventory migration stopped before DDL: historical SKU is orphaned or its merchant has no warehouse; "
                f"sample SKU/SPU pairs: {bad_rows}"
            )

    # Keep historical updated_at values unchanged while deriving tenant identity.
    op.add_column("skus", sa.Column("merchant_id", sa.BigInteger(), nullable=True))
    op.execute(
        "UPDATE skus AS s JOIN spus AS p ON p.id = s.spu_id "
        "SET s.merchant_id = p.merchant_id, s.updated_at = s.updated_at"
    )
    if not context.as_sql:
        missing = bind.scalar(sa.text("SELECT COUNT(*) FROM skus WHERE merchant_id IS NULL"))
        if missing:
            raise RuntimeError(f"inventory migration backfill incomplete: {missing} SKU rows have no merchant")
    op.alter_column("skus", "merchant_id", existing_type=sa.BigInteger(), nullable=False)
    op.create_unique_constraint("uq_spus_merchant_id_id", "spus", ["merchant_id", "id"])
    op.create_unique_constraint("uq_skus_merchant_id_id", "skus", ["merchant_id", "id"])
    op.create_foreign_key(
        "fk_skus_merchant_spu", "skus", "spus", ["merchant_id", "spu_id"], ["merchant_id", "id"],
        ondelete="RESTRICT", onupdate="RESTRICT",
    )
    op.create_index("ix_skus_merchant_id", "skus", ["merchant_id"])

    op.create_table(
        "stocks",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("merchant_id", sa.BigInteger(), nullable=False),
        sa.Column("warehouse_id", sa.BigInteger(), nullable=False),
        sa.Column("sku_id", sa.BigInteger(), nullable=False),
        sa.Column("quantity", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP"), nullable=False),
        sa.CheckConstraint("quantity >= 0", name=op.f("ck_stocks_quantity_nonnegative")),
        sa.ForeignKeyConstraint(["merchant_id", "warehouse_id"], ["warehouses.merchant_id", "warehouses.id"],
                                name="fk_stocks_merchant_warehouse", ondelete="RESTRICT", onupdate="RESTRICT"),
        sa.ForeignKeyConstraint(["merchant_id", "sku_id"], ["skus.merchant_id", "skus.id"],
                                name="fk_stocks_merchant_sku", ondelete="RESTRICT", onupdate="RESTRICT"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_stocks")),
        sa.UniqueConstraint("warehouse_id", "sku_id", name="uq_stocks_warehouse_sku"),
        sa.UniqueConstraint("merchant_id", "warehouse_id", "sku_id", name="uq_stocks_merchant_warehouse_sku"),
        mysql_charset="utf8mb4", mysql_collate="utf8mb4_unicode_ci", mysql_engine="InnoDB",
    )
    op.create_index("ix_stocks_merchant_sku", "stocks", ["merchant_id", "sku_id"])
    op.create_table(
        "stock_movements",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("merchant_id", sa.BigInteger(), nullable=False),
        sa.Column("warehouse_id", sa.BigInteger(), nullable=False),
        sa.Column("sku_id", sa.BigInteger(), nullable=False),
        sa.Column("actor_user_id", sa.BigInteger(), nullable=False),
        sa.Column("operation_type", sa.String(length=32, collation="utf8mb4_bin"), nullable=False),
        sa.Column("quantity_delta", sa.BigInteger(), nullable=False),
        sa.Column("quantity_before", sa.BigInteger(), nullable=False),
        sa.Column("quantity_after", sa.BigInteger(), nullable=False),
        sa.Column("idempotency_key", sa.String(length=128, collation="utf8mb4_bin"), nullable=False),
        sa.Column("request_hash", sa.String(length=64, collation="ascii_bin"), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.CheckConstraint("operation_type = 'MANUAL_ADJUSTMENT'", name=op.f("ck_stock_movements_operation_type_manual_adjustment")),
        sa.CheckConstraint("quantity_delta <> 0", name=op.f("ck_stock_movements_quantity_delta_nonzero")),
        sa.CheckConstraint("quantity_before >= 0 AND quantity_after >= 0", name=op.f("ck_stock_movements_quantities_nonnegative")),
        sa.CheckConstraint(
            "CAST(quantity_before AS DECIMAL(20,0)) + CAST(quantity_delta AS DECIMAL(20,0)) = "
            "CAST(quantity_after AS DECIMAL(20,0))",
            name=op.f("ck_stock_movements_delta_matches_quantities"),
        ),
        sa.ForeignKeyConstraint(["merchant_id", "warehouse_id"], ["warehouses.merchant_id", "warehouses.id"],
                                name="fk_stock_movements_merchant_warehouse", ondelete="RESTRICT", onupdate="RESTRICT"),
        sa.ForeignKeyConstraint(["merchant_id", "sku_id"], ["skus.merchant_id", "skus.id"],
                                name="fk_stock_movements_merchant_sku", ondelete="RESTRICT", onupdate="RESTRICT"),
        sa.ForeignKeyConstraint(["merchant_id", "warehouse_id", "sku_id"],
                                ["stocks.merchant_id", "stocks.warehouse_id", "stocks.sku_id"],
                                name="fk_stock_movements_stock", ondelete="RESTRICT", onupdate="RESTRICT"),
        sa.ForeignKeyConstraint(["actor_user_id"], ["users.id"], name="fk_stock_movements_actor", ondelete="RESTRICT", onupdate="RESTRICT"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_stock_movements")),
        sa.UniqueConstraint("merchant_id", "idempotency_key", name="uq_stock_movements_merchant_idempotency"),
        mysql_charset="utf8mb4", mysql_collate="utf8mb4_unicode_ci", mysql_engine="InnoDB",
    )
    op.create_index("ix_stock_movements_merchant_sku_id", "stock_movements", ["merchant_id", "sku_id", "id"])

    op.execute(
        "INSERT INTO stocks (merchant_id, warehouse_id, sku_id, quantity) "
        "SELECT p.merchant_id, w.id, s.id, 0 FROM skus AS s "
        "JOIN spus AS p ON p.id = s.spu_id JOIN warehouses AS w ON w.merchant_id = p.merchant_id "
        "ORDER BY s.id"
    )
    if not context.as_sql:
        sku_count, stock_count = bind.execute(sa.text(
            "SELECT (SELECT COUNT(*) FROM skus), (SELECT COUNT(*) FROM stocks)"
        )).one()
        if sku_count != stock_count:
            raise RuntimeError(f"inventory migration backfill mismatch: {sku_count} SKUs, {stock_count} stocks")


def downgrade() -> None:
    op.drop_table("stock_movements")
    op.drop_table("stocks")
    op.drop_index("ix_skus_merchant_id", table_name="skus")
    op.drop_constraint("fk_skus_merchant_spu", "skus", type_="foreignkey")
    op.drop_constraint("uq_skus_merchant_id_id", "skus", type_="unique")
    op.drop_column("skus", "merchant_id")
    op.drop_constraint("uq_spus_merchant_id_id", "spus", type_="unique")
