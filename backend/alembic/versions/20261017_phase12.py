"""merchant profile, owner handoff and scoped audit query support

Revision ID: 20261017_phase12
Revises: 20261017_orders
Create Date: 2026-10-17
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import text

revision: str = "20261017_phase12"
down_revision: Union[str, Sequence[str], None] = "20261017_orders"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("shops", sa.Column("owner_restore_role_id", sa.BigInteger(), nullable=True))
    op.add_column("shops", sa.Column("pending_owner_proposal_id", sa.String(36, collation="ascii_bin"), nullable=True))
    op.add_column("shops", sa.Column("pending_owner_from_user_id", sa.BigInteger(), nullable=True))
    op.add_column("shops", sa.Column("pending_owner_target_user_id", sa.BigInteger(), nullable=True))
    op.add_column("shops", sa.Column("last_owner_transfer_kind", sa.String(16, collation="ascii_bin"), nullable=True))
    op.add_column("shops", sa.Column("last_owner_transfer_outcome", sa.String(16, collation="ascii_bin"), nullable=True))
    op.add_column("shops", sa.Column("last_owner_transfer_reason", sa.Text(), nullable=True))
    op.add_column("shops", sa.Column("last_owner_transfer_from_user_id", sa.BigInteger(), nullable=True))
    op.add_column("shops", sa.Column("last_owner_transfer_to_user_id", sa.BigInteger(), nullable=True))
    op.add_column("shops", sa.Column("last_owner_transfer_at", sa.DateTime(), nullable=True))
    op.create_unique_constraint("uq_shops_pending_owner_proposal", "shops", ["pending_owner_proposal_id"])
    op.create_foreign_key("fk_shops_pending_owner_from", "shops", "users", ["pending_owner_from_user_id"], ["id"], ondelete="RESTRICT", onupdate="RESTRICT")
    op.create_foreign_key("fk_shops_pending_owner_target", "shops", "users", ["pending_owner_target_user_id"], ["id"], ondelete="RESTRICT", onupdate="RESTRICT")
    op.create_foreign_key("fk_shops_last_transfer_from", "shops", "users", ["last_owner_transfer_from_user_id"], ["id"], ondelete="RESTRICT", onupdate="RESTRICT")
    op.create_foreign_key("fk_shops_last_transfer_to", "shops", "users", ["last_owner_transfer_to_user_id"], ["id"], ondelete="RESTRICT", onupdate="RESTRICT")
    op.create_foreign_key(
        "fk_shops_owner_restore_role", "shops", "shop_employee_roles",
        ["id", "owner_restore_role_id"], ["shop_id", "id"],
        ondelete="RESTRICT", onupdate="RESTRICT",
    )
    op.create_check_constraint(
        op.f("ck_shops_pending_owner_proposal_fields"),
        "shops",
        "(pending_owner_proposal_id IS NULL AND pending_owner_from_user_id IS NULL AND pending_owner_target_user_id IS NULL) OR "
        "(pending_owner_proposal_id IS NOT NULL AND pending_owner_from_user_id IS NOT NULL AND pending_owner_target_user_id IS NOT NULL "
        "AND pending_owner_from_user_id <> pending_owner_target_user_id)",
    )
    op.create_check_constraint(
        op.f("ck_shops_last_owner_transfer_fields"),
        "shops",
        "(last_owner_transfer_kind IS NULL AND last_owner_transfer_outcome IS NULL AND last_owner_transfer_from_user_id IS NULL "
        "AND last_owner_transfer_to_user_id IS NULL AND last_owner_transfer_at IS NULL AND last_owner_transfer_reason IS NULL) OR "
        "(last_owner_transfer_kind IS NOT NULL AND last_owner_transfer_outcome IS NOT NULL "
        "AND last_owner_transfer_from_user_id IS NOT NULL AND last_owner_transfer_to_user_id IS NOT NULL "
        "AND last_owner_transfer_from_user_id <> last_owner_transfer_to_user_id AND last_owner_transfer_at IS NOT NULL "
        "AND ((last_owner_transfer_kind = 'NORMAL' AND last_owner_transfer_outcome = 'COMPLETED' "
        "AND last_owner_transfer_reason IS NULL) OR "
        "(last_owner_transfer_kind = 'FORCE' AND last_owner_transfer_outcome = 'FORCED' "
        "AND last_owner_transfer_reason IS NOT NULL AND CHAR_LENGTH(TRIM(last_owner_transfer_reason)) > 0)))",
    )


def downgrade() -> None:
    bind = op.get_bind()
    rows = bind.execute(text(
        "SELECT id FROM shops WHERE owner_restore_role_id IS NOT NULL "
        "OR pending_owner_proposal_id IS NOT NULL OR pending_owner_from_user_id IS NOT NULL "
        "OR pending_owner_target_user_id IS NOT NULL OR last_owner_transfer_kind IS NOT NULL "
        "OR last_owner_transfer_outcome IS NOT NULL OR last_owner_transfer_reason IS NOT NULL "
        "OR last_owner_transfer_from_user_id IS NOT NULL OR last_owner_transfer_to_user_id IS NOT NULL "
        "OR last_owner_transfer_at IS NOT NULL ORDER BY id LIMIT 5"
    )).scalars().all()
    if rows:
        raise RuntimeError(
            "refusing phase12 downgrade: shop owner handoff/restore data exists "
            f"(shop IDs sample: {', '.join(map(str, rows))}); preserve consent, reason, and role provenance"
        )
    op.drop_constraint(op.f("ck_shops_last_owner_transfer_fields"), "shops", type_="check")
    op.drop_constraint(op.f("ck_shops_pending_owner_proposal_fields"), "shops", type_="check")
    op.drop_constraint("fk_shops_owner_restore_role", "shops", type_="foreignkey")
    op.drop_constraint("fk_shops_last_transfer_to", "shops", type_="foreignkey")
    op.drop_constraint("fk_shops_last_transfer_from", "shops", type_="foreignkey")
    op.drop_constraint("fk_shops_pending_owner_target", "shops", type_="foreignkey")
    op.drop_constraint("fk_shops_pending_owner_from", "shops", type_="foreignkey")
    op.drop_constraint("uq_shops_pending_owner_proposal", "shops", type_="unique")
    for column in (
        "last_owner_transfer_at", "last_owner_transfer_to_user_id", "last_owner_transfer_from_user_id",
        "last_owner_transfer_reason", "last_owner_transfer_outcome", "last_owner_transfer_kind",
        "pending_owner_target_user_id", "pending_owner_from_user_id", "pending_owner_proposal_id",
        "owner_restore_role_id",
    ):
        op.drop_column("shops", column)
