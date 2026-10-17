"""merchant catalog categories, SPUs and SKUs

Revision ID: 20261017_catalog
Revises: a6e73c92f014
Create Date: 2026-10-17
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20261017_catalog"
down_revision: Union[str, Sequence[str], None] = "a6e73c92f014"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "categories",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("merchant_id", sa.BigInteger(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.ForeignKeyConstraint(
            ["merchant_id"], ["merchants.id"], name="fk_categories_merchant_id_merchants",
            ondelete="RESTRICT", onupdate="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_categories"),
        sa.UniqueConstraint("merchant_id", "name", name="uq_categories_merchant_name"),
        sa.UniqueConstraint("merchant_id", "id", name="uq_categories_merchant_id"),
        mysql_charset="utf8mb4", mysql_collate="utf8mb4_unicode_ci", mysql_engine="InnoDB",
    )

    op.create_table(
        "spus",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("merchant_id", sa.BigInteger(), nullable=False),
        sa.Column("category_id", sa.BigInteger(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=16, collation="utf8mb4_bin"), server_default="OFF_SALE", nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column(
            "updated_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.CheckConstraint("status IN ('OFF_SALE', 'ON_SALE')", name=op.f("ck_spus_status")),
        sa.ForeignKeyConstraint(
            ["merchant_id"], ["merchants.id"], name="fk_spus_merchant_id_merchants",
            ondelete="RESTRICT", onupdate="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["merchant_id", "category_id"], ["categories.merchant_id", "categories.id"],
            name="fk_spus_merchant_category", ondelete="RESTRICT", onupdate="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_spus"),
        mysql_charset="utf8mb4", mysql_collate="utf8mb4_unicode_ci", mysql_engine="InnoDB",
    )
    op.create_index("ix_spus_merchant_category", "spus", ["merchant_id", "category_id"], unique=False)

    op.create_table(
        "skus",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("spu_id", sa.BigInteger(), nullable=False),
        sa.Column("sku_code", sa.String(length=100), nullable=False),
        sa.Column("specification", sa.String(length=1000), nullable=False),
        sa.Column("price", sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column(
            "updated_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.CheckConstraint("price >= 0", name=op.f("ck_skus_price_nonnegative")),
        sa.ForeignKeyConstraint(
            ["spu_id"], ["spus.id"], name="fk_skus_spu_id_spus", ondelete="RESTRICT", onupdate="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_skus"),
        sa.UniqueConstraint("spu_id", "sku_code", name="uq_skus_spu_code"),
        mysql_charset="utf8mb4", mysql_collate="utf8mb4_unicode_ci", mysql_engine="InnoDB",
    )


def downgrade() -> None:
    # Drop whole tables in dependency order so MySQL does not reject FK support index removal.
    op.drop_table("skus")
    op.drop_table("spus")
    op.drop_table("categories")
