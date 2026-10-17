from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Computed,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Category(Base):
    __tablename__ = "categories"
    __table_args__ = (
        UniqueConstraint("merchant_id", "name", name="uq_categories_merchant_name"),
        # MySQL requires an explicit unique referenced key for the SPU tenant FK.
        UniqueConstraint("merchant_id", "id", name="uq_categories_merchant_id"),
        {"mysql_engine": "InnoDB", "mysql_charset": "utf8mb4", "mysql_collate": "utf8mb4_unicode_ci"},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    merchant_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("merchants.id", ondelete="RESTRICT", onupdate="RESTRICT"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP"))


class Spu(Base):
    __tablename__ = "spus"
    __table_args__ = (
        ForeignKeyConstraint(
            ["merchant_id", "category_id"], ["categories.merchant_id", "categories.id"],
            name="fk_spus_merchant_category", ondelete="RESTRICT", onupdate="RESTRICT",
        ),
        CheckConstraint("status IN ('OFF_SALE', 'ON_SALE')", name="status"),
        UniqueConstraint("merchant_id", "id", name="uq_spus_merchant_id_id"),
        Index("ix_spus_merchant_category", "merchant_id", "category_id"),
        {"mysql_engine": "InnoDB", "mysql_charset": "utf8mb4", "mysql_collate": "utf8mb4_unicode_ci"},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    merchant_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("merchants.id", ondelete="RESTRICT", onupdate="RESTRICT"), nullable=False
    )
    category_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(
        String(16, collation="utf8mb4_bin"), nullable=False, server_default="OFF_SALE"
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP"))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False,
        server_default=text("CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP"),
        onupdate=func.current_timestamp(),
    )


class Sku(Base):
    __tablename__ = "skus"
    __table_args__ = (
        CheckConstraint("price >= 0", name="price_nonnegative"),
        UniqueConstraint("spu_id", "sku_code", name="uq_skus_spu_code"),
        UniqueConstraint("merchant_id", "id", name="uq_skus_merchant_id_id"),
        ForeignKeyConstraint(
            ["merchant_id", "spu_id"], ["spus.merchant_id", "spus.id"],
            name="fk_skus_merchant_spu", ondelete="RESTRICT", onupdate="RESTRICT",
        ),
        {"mysql_engine": "InnoDB", "mysql_charset": "utf8mb4", "mysql_collate": "utf8mb4_unicode_ci"},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    merchant_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    spu_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("spus.id", ondelete="RESTRICT", onupdate="RESTRICT"), nullable=False
    )
    sku_code: Mapped[str] = mapped_column(String(100), nullable=False)
    specification: Mapped[str] = mapped_column(String(1000), nullable=False)
    price: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP"))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False,
        server_default=text("CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP"),
        onupdate=func.current_timestamp(),
    )


class ProductOffshelfApplication(Base):
    __tablename__ = "product_offshelf_applications"
    __table_args__ = (
        CheckConstraint("status IN ('PENDING', 'APPROVED', 'REJECTED')", name="status"),
        UniqueConstraint("pending_spu_id", name="uq_product_offshelf_applications_pending_spu"),
        Index("ix_product_offshelf_applications_status_id", "status", "id"),
        {"mysql_engine": "InnoDB", "mysql_charset": "utf8mb4", "mysql_collate": "utf8mb4_unicode_ci"},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    spu_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("spus.id", ondelete="RESTRICT", onupdate="RESTRICT"), nullable=False, index=True
    )
    applicant_user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="RESTRICT", onupdate="RESTRICT"), nullable=False, index=True
    )
    status: Mapped[str] = mapped_column(
        String(16, collation="utf8mb4_bin"), nullable=False, server_default="PENDING"
    )
    reviewer_user_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="RESTRICT", onupdate="RESTRICT")
    )
    rejection_reason: Mapped[str | None] = mapped_column(String(2000))
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP"))
    pending_spu_id: Mapped[int | None] = mapped_column(
        BigInteger, Computed("CASE WHEN status = 'PENDING' THEN spu_id ELSE NULL END", persisted=True)
    )
