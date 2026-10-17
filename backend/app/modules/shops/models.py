from datetime import datetime

from sqlalchemy import BigInteger, CheckConstraint, DateTime, ForeignKey, ForeignKeyConstraint, String, Text, UniqueConstraint, func, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class Shop(Base):
    __tablename__ = "shops"
    __table_args__ = (
        UniqueConstraint("merchant_id", "name", name="uq_shops_merchant_name"),
        UniqueConstraint("merchant_id", "id", name="uq_shops_merchant_id"),
        CheckConstraint(
            "(pending_owner_proposal_id IS NULL AND pending_owner_from_user_id IS NULL AND pending_owner_target_user_id IS NULL) OR "
            "(pending_owner_proposal_id IS NOT NULL AND pending_owner_from_user_id IS NOT NULL AND pending_owner_target_user_id IS NOT NULL "
            "AND pending_owner_from_user_id <> pending_owner_target_user_id)",
            name="pending_owner_proposal_fields",
        ),
        CheckConstraint(
            "(last_owner_transfer_kind IS NULL AND last_owner_transfer_outcome IS NULL AND last_owner_transfer_from_user_id IS NULL "
            "AND last_owner_transfer_to_user_id IS NULL AND last_owner_transfer_at IS NULL AND last_owner_transfer_reason IS NULL) OR "
            "(last_owner_transfer_kind IN ('NORMAL', 'FORCE') AND last_owner_transfer_outcome IN ('COMPLETED', 'FORCED') "
            "AND last_owner_transfer_from_user_id IS NOT NULL AND last_owner_transfer_to_user_id IS NOT NULL "
            "AND last_owner_transfer_from_user_id <> last_owner_transfer_to_user_id AND last_owner_transfer_at IS NOT NULL "
            "AND ((last_owner_transfer_kind = 'FORCE' AND last_owner_transfer_reason IS NOT NULL) OR "
            "(last_owner_transfer_kind = 'NORMAL' AND last_owner_transfer_outcome = 'COMPLETED' AND last_owner_transfer_reason IS NULL)))",
            name="last_owner_transfer_fields",
        ),
        UniqueConstraint("pending_owner_proposal_id", name="uq_shops_pending_owner_proposal"),
        ForeignKeyConstraint(
            ["id", "owner_restore_role_id"], ["shop_employee_roles.shop_id", "shop_employee_roles.id"],
            name="fk_shops_owner_restore_role", ondelete="RESTRICT", onupdate="RESTRICT", use_alter=True,
        ),
        {"mysql_engine": "InnoDB", "mysql_charset": "utf8mb4", "mysql_collate": "utf8mb4_unicode_ci"},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    merchant_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("merchants.id", ondelete="RESTRICT", onupdate="RESTRICT"),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    owner_user_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("users.id", ondelete="RESTRICT", onupdate="RESTRICT"),
        nullable=False,
        unique=True,
    )
    owner_restore_role_id: Mapped[int | None] = mapped_column(BigInteger)
    pending_owner_proposal_id: Mapped[str | None] = mapped_column(String(36, collation="ascii_bin"))
    pending_owner_from_user_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id", name="fk_shops_pending_owner_from", ondelete="RESTRICT", onupdate="RESTRICT"),
    )
    pending_owner_target_user_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id", name="fk_shops_pending_owner_target", ondelete="RESTRICT", onupdate="RESTRICT"),
    )
    last_owner_transfer_kind: Mapped[str | None] = mapped_column(String(16, collation="ascii_bin"))
    last_owner_transfer_outcome: Mapped[str | None] = mapped_column(String(16, collation="ascii_bin"))
    last_owner_transfer_reason: Mapped[str | None] = mapped_column(Text)
    last_owner_transfer_from_user_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id", name="fk_shops_last_transfer_from", ondelete="RESTRICT", onupdate="RESTRICT"),
    )
    last_owner_transfer_to_user_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id", name="fk_shops_last_transfer_to", ondelete="RESTRICT", onupdate="RESTRICT"),
    )
    last_owner_transfer_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP"))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP"),
        onupdate=func.current_timestamp(),
    )

    merchant: Mapped["Merchant"] = relationship(back_populates="shops")
    owner_user: Mapped["User"] = relationship(back_populates="owned_shop", foreign_keys=[owner_user_id])


class ShopApplication(Base):
    __tablename__ = "shop_applications"
    __table_args__ = (
        CheckConstraint("status IN ('PENDING', 'APPROVED', 'REJECTED')", name="shop_application_status"),
        {"mysql_engine": "InnoDB", "mysql_charset": "utf8mb4", "mysql_collate": "utf8mb4_unicode_ci"},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    merchant_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("merchants.id", ondelete="RESTRICT", onupdate="RESTRICT"),
        nullable=False,
        index=True,
    )
    applicant_user_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("users.id", ondelete="RESTRICT", onupdate="RESTRICT"),
        nullable=False,
        index=True,
    )
    shop_name: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(
        String(16, collation="utf8mb4_bin"), nullable=False, server_default="PENDING"
    )
    reviewer_user_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("users.id", ondelete="RESTRICT", onupdate="RESTRICT"),
    )
    rejection_reason: Mapped[str | None] = mapped_column(Text)
    created_shop_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("shops.id", ondelete="RESTRICT", onupdate="RESTRICT"),
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP"))
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime)
