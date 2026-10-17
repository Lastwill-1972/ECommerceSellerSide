"""Warehouse access applications and explicit shop grants.

Revision ID: 20261017_warehouse_access
Revises: 20261017_offshelf
Create Date: 2026-10-17
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20261017_warehouse_access"
down_revision: Union[str, Sequence[str], None] = "20261017_offshelf"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Explicit parent keys let the database enforce that a granted shop and
    # warehouse belong to the same merchant without changing existing rows.
    op.create_unique_constraint("uq_shops_merchant_id", "shops", ["merchant_id", "id"])
    op.create_unique_constraint("uq_warehouses_merchant_id_id", "warehouses", ["merchant_id", "id"])
    op.create_table(
        "warehouse_access_applications",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("shop_id", sa.BigInteger(), nullable=False),
        sa.Column("applicant_user_id", sa.BigInteger(), nullable=False),
        sa.Column("status", sa.String(length=16, collation="utf8mb4_bin"), server_default="PENDING", nullable=False),
        sa.Column("reviewer_user_id", sa.BigInteger(), nullable=True),
        sa.Column("rejection_reason", sa.String(length=2000), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("reviewed_at", sa.DateTime(), nullable=True),
        sa.Column(
            "pending_shop_id", sa.BigInteger(),
            sa.Computed("CASE WHEN status = 'PENDING' THEN shop_id ELSE NULL END", persisted=True), nullable=True,
        ),
        sa.CheckConstraint("status IN ('PENDING', 'APPROVED', 'REJECTED')", name=op.f("ck_warehouse_access_applications_status")),
        sa.ForeignKeyConstraint(["shop_id"], ["shops.id"], name="fk_warehouse_access_applications_shop_id_shops", ondelete="RESTRICT", onupdate="RESTRICT"),
        sa.ForeignKeyConstraint(["applicant_user_id"], ["users.id"], name="fk_warehouse_access_applications_applicant_user_id_users", ondelete="RESTRICT", onupdate="RESTRICT"),
        sa.ForeignKeyConstraint(["reviewer_user_id"], ["users.id"], name="fk_warehouse_access_applications_reviewer_user_id_users", ondelete="RESTRICT", onupdate="RESTRICT"),
        sa.PrimaryKeyConstraint("id", name="pk_warehouse_access_applications"),
        sa.UniqueConstraint("pending_shop_id", name="uq_warehouse_access_applications_pending_shop"),
        mysql_charset="utf8mb4", mysql_collate="utf8mb4_unicode_ci", mysql_engine="InnoDB",
    )
    op.create_index("ix_warehouse_access_applications_shop_id", "warehouse_access_applications", ["shop_id"])
    op.create_index("ix_warehouse_access_applications_applicant_user_id", "warehouse_access_applications", ["applicant_user_id"])
    op.create_index("ix_warehouse_access_applications_status_id", "warehouse_access_applications", ["status", "id"])
    op.create_table(
        "warehouse_accesses",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("shop_id", sa.BigInteger(), nullable=False),
        sa.Column("warehouse_id", sa.BigInteger(), nullable=False),
        sa.Column("merchant_id", sa.BigInteger(), nullable=False),
        sa.Column("granted_by_user_id", sa.BigInteger(), nullable=False),
        sa.Column("granted_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.ForeignKeyConstraint(["merchant_id", "shop_id"], ["shops.merchant_id", "shops.id"], name="fk_warehouse_accesses_merchant_shop", ondelete="RESTRICT", onupdate="RESTRICT"),
        sa.ForeignKeyConstraint(["merchant_id", "warehouse_id"], ["warehouses.merchant_id", "warehouses.id"], name="fk_warehouse_accesses_merchant_warehouse", ondelete="RESTRICT", onupdate="RESTRICT"),
        sa.ForeignKeyConstraint(["granted_by_user_id"], ["users.id"], name="fk_warehouse_accesses_granted_by_user_id_users", ondelete="RESTRICT", onupdate="RESTRICT"),
        sa.PrimaryKeyConstraint("id", name="pk_warehouse_accesses"),
        sa.UniqueConstraint("shop_id", name="uq_warehouse_accesses_shop_id"),
        mysql_charset="utf8mb4", mysql_collate="utf8mb4_unicode_ci", mysql_engine="InnoDB",
    )
    op.create_index("ix_warehouse_accesses_merchant_id", "warehouse_accesses", ["merchant_id"])


def downgrade() -> None:
    op.drop_table("warehouse_accesses")
    op.drop_table("warehouse_access_applications")
    op.drop_constraint("uq_warehouses_merchant_id_id", "warehouses", type_="unique")
    op.drop_constraint("uq_shops_merchant_id", "shops", type_="unique")
