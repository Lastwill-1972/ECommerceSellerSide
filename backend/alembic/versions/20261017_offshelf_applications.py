"""SPU offshelf applications and review history.

Revision ID: 20261017_offshelf
Revises: 20261017_catalog
Create Date: 2026-10-17
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20261017_offshelf"
down_revision: Union[str, Sequence[str], None] = "20261017_catalog"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "product_offshelf_applications",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("spu_id", sa.BigInteger(), nullable=False),
        sa.Column("applicant_user_id", sa.BigInteger(), nullable=False),
        sa.Column("status", sa.String(length=16, collation="utf8mb4_bin"), server_default="PENDING", nullable=False),
        sa.Column("reviewer_user_id", sa.BigInteger(), nullable=True),
        sa.Column("rejection_reason", sa.String(length=2000), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column(
            "pending_spu_id", sa.BigInteger(),
            sa.Computed("CASE WHEN status = 'PENDING' THEN spu_id ELSE NULL END", persisted=True),
            nullable=True,
        ),
        sa.CheckConstraint("status IN ('PENDING', 'APPROVED', 'REJECTED')", name=op.f("ck_product_offshelf_applications_status")),
        sa.ForeignKeyConstraint(
            ["spu_id"], ["spus.id"], name="fk_product_offshelf_applications_spu_id_spus",
            ondelete="RESTRICT", onupdate="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["applicant_user_id"], ["users.id"], name="fk_product_offshelf_applications_applicant_user_id_users",
            ondelete="RESTRICT", onupdate="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["reviewer_user_id"], ["users.id"], name="fk_product_offshelf_applications_reviewer_user_id_users",
            ondelete="RESTRICT", onupdate="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_product_offshelf_applications"),
        sa.UniqueConstraint("pending_spu_id", name="uq_product_offshelf_applications_pending_spu"),
        mysql_charset="utf8mb4", mysql_collate="utf8mb4_unicode_ci", mysql_engine="InnoDB",
    )
    op.create_index("ix_product_offshelf_applications_spu_id", "product_offshelf_applications", ["spu_id"])
    op.create_index(
        "ix_product_offshelf_applications_applicant_user_id", "product_offshelf_applications", ["applicant_user_id"]
    )
    op.create_index(
        "ix_product_offshelf_applications_status_id", "product_offshelf_applications", ["status", "id"]
    )


def downgrade() -> None:
    op.drop_table("product_offshelf_applications")
