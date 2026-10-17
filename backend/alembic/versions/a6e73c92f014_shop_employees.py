"""shop employees, roles and applications

Revision ID: a6e73c92f014
Revises: 789fe3dda218
Create Date: 2026-10-16
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "a6e73c92f014"
down_revision: Union[str, Sequence[str], None] = "789fe3dda218"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "shop_employee_roles",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("shop_id", sa.BigInteger(), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.ForeignKeyConstraint(
            ["shop_id"], ["shops.id"], name="fk_shop_employee_roles_shop_id_shops",
            onupdate="RESTRICT", ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_shop_employee_roles"),
        sa.UniqueConstraint("shop_id", "name", name="uq_shop_employee_roles_shop_name"),
        sa.UniqueConstraint("shop_id", "id", name="uq_shop_employee_roles_shop_id"),
        mysql_charset="utf8mb4", mysql_collate="utf8mb4_unicode_ci", mysql_engine="InnoDB",
    )
    op.create_index("ix_shop_employee_roles_shop_id", "shop_employee_roles", ["shop_id"], unique=False)

    # Safely backfill the ordinary employee role for every existing shop.
    op.execute("INSERT INTO shop_employee_roles (shop_id, name) SELECT id, '普通员工' FROM shops")

    op.create_table(
        "shop_employees",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("shop_id", sa.BigInteger(), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("role_id", sa.BigInteger(), nullable=False),
        sa.Column("joined_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.ForeignKeyConstraint(
            ["shop_id"], ["shops.id"], name="fk_shop_employees_shop_id_shops",
            onupdate="RESTRICT", ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name="fk_shop_employees_user_id_users",
            onupdate="RESTRICT", ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["shop_id", "role_id"], ["shop_employee_roles.shop_id", "shop_employee_roles.id"],
            name="fk_shop_employees_shop_role", onupdate="RESTRICT", ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_shop_employees"),
        sa.UniqueConstraint("user_id", name="uq_shop_employees_user"),
        mysql_charset="utf8mb4", mysql_collate="utf8mb4_unicode_ci", mysql_engine="InnoDB",
    )
    op.create_index("ix_shop_employees_shop_id", "shop_employees", ["shop_id"], unique=False)

    op.create_table(
        "employee_applications",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("shop_id", sa.BigInteger(), nullable=False),
        sa.Column("applicant_user_id", sa.BigInteger(), nullable=False),
        sa.Column("status", sa.String(length=16, collation="utf8mb4_bin"), server_default="PENDING", nullable=False),
        sa.Column("reviewer_user_id", sa.BigInteger(), nullable=True),
        sa.Column("rejection_reason", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("reviewed_at", sa.DateTime(), nullable=True),
        sa.CheckConstraint("status IN ('PENDING', 'APPROVED', 'REJECTED')", name="ck_employee_applications_employee_application_status"),
        sa.ForeignKeyConstraint(
            ["shop_id"], ["shops.id"], name="fk_employee_applications_shop_id_shops",
            onupdate="RESTRICT", ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["applicant_user_id"], ["users.id"], name="fk_employee_applications_applicant_user_id_users",
            onupdate="RESTRICT", ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["reviewer_user_id"], ["users.id"], name="fk_employee_applications_reviewer_user_id_users",
            onupdate="RESTRICT", ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_employee_applications"),
        mysql_charset="utf8mb4", mysql_collate="utf8mb4_unicode_ci", mysql_engine="InnoDB",
    )
    op.create_index("ix_employee_applications_shop_id", "employee_applications", ["shop_id"], unique=False)
    op.create_index("ix_employee_applications_applicant_user_id", "employee_applications", ["applicant_user_id"], unique=False)


def downgrade() -> None:
    # Drop complete tables in reverse FK dependency order. MySQL drops supporting
    # indexes with the table, avoiding error 1553 from a preceding index drop.
    op.drop_table("employee_applications")
    op.drop_table("shop_employees")
    op.drop_table("shop_employee_roles")
