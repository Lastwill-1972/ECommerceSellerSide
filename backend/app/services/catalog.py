from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.modules.catalog.models import Category, Spu, Sku
from app.modules.inventory.models import Stock, Warehouse
from app.modules.catalog.schemas import (
    CategoryPage, CategoryResponse, SpuDetail, SpuPage, SpuResponse, SpuStatus,
    SkuResponse,
)
from app.modules.users.models import User
from app.services.audit import AuditService
from app.core.permissions import AccessDenied, AuthorizationService
from app.services.identity import IdentityConflict, IdentityEligibilityError, IdentityService


class CatalogForbidden(Exception):
    pass


class CatalogNotFound(Exception):
    pass


class CatalogConflict(Exception):
    pass


def _fresh(session: Session) -> None:
    if session.in_transaction():
        raise RuntimeError("catalog write requires a fresh transaction boundary")


def _tenant(identity, *, write: bool) -> int:
    try:
        return AuthorizationService.catalog_merchant(identity, write=write)
    except AccessDenied as exc:
        raise CatalogForbidden("no catalog access for this identity") from exc


def _locked_merchant(session: Session, actor_user_id: int) -> int:
    user = session.scalar(
        select(User).where(User.id == actor_user_id).with_for_update().execution_options(populate_existing=True)
    )
    if user is None or not user.is_active:
        raise CatalogForbidden("active merchant administrator required")
    try:
        identity = IdentityService.get_identity(session, user)
    except (IdentityConflict, IdentityEligibilityError) as exc:
        raise CatalogForbidden("active merchant administrator required") from exc
    try:
        merchant_id = AuthorizationService.catalog_merchant(identity, write=True)
    except AccessDenied as exc:
        raise CatalogForbidden("active merchant administrator required") from exc
    return int(merchant_id)


def _page(items, limit: int, offset: int, total: int):
    return {"items": items, "limit": limit, "offset": offset, "total": total}


def _category_response(row: Category) -> CategoryResponse:
    return CategoryResponse.model_validate(row, from_attributes=True)


def _spu_response(row: Spu) -> SpuResponse:
    return SpuResponse.model_validate(row, from_attributes=True)


def _sku_response(row: Sku) -> SkuResponse:
    return SkuResponse.model_validate(row, from_attributes=True)


def _duplicate(exc: IntegrityError) -> bool:
    args = getattr(exc.orig, "args", ())
    return bool(args and args[0] == 1062)


class CatalogService:
    @staticmethod
    def list_categories(session: Session, *, identity, limit: int, offset: int) -> CategoryPage:
        merchant_id = _tenant(identity, write=False)
        scope = Category.merchant_id == merchant_id
        total = session.scalar(select(func.count(Category.id)).where(scope)) or 0
        rows = session.scalars(select(Category).where(scope).order_by(Category.id).limit(limit).offset(offset)).all()
        return CategoryPage(**_page([_category_response(row) for row in rows], limit, offset, total))

    @staticmethod
    def create_category(
        session: Session, *, actor_user_id: int, name: str, audit_writer=AuditService.record_after_commit
    ) -> CategoryResponse:
        _fresh(session)
        try:
            with session.begin():
                merchant_id = _locked_merchant(session, actor_user_id)
                row = Category(merchant_id=merchant_id, name=name)
                session.add(row)
                session.flush()
                session.refresh(row)
                response = _category_response(row)
                target_id = row.id
        except IntegrityError as exc:
            if _duplicate(exc):
                raise CatalogConflict("category name already exists for this merchant") from exc
            raise
        audit_writer(
            actor_user_id=actor_user_id, event_type="catalog_category_created", target_type="category",
            target_id=target_id, outcome="CREATED", event_data={"merchant_id": merchant_id},
        )
        return response

    @staticmethod
    def list_spus(
        session: Session, *, identity, limit: int, offset: int, category_id: int | None, status_filter: SpuStatus | None
    ) -> SpuPage:
        merchant_id = _tenant(identity, write=False)
        scope = [Spu.merchant_id == merchant_id]
        if category_id is not None:
            scope.append(Spu.category_id == category_id)
        if status_filter is not None:
            scope.append(Spu.status == status_filter.value)
        total = session.scalar(select(func.count(Spu.id)).where(*scope)) or 0
        rows = session.scalars(select(Spu).where(*scope).order_by(Spu.id).limit(limit).offset(offset)).all()
        return SpuPage(**_page([_spu_response(row) for row in rows], limit, offset, total))

    @staticmethod
    def create_spu(
        session: Session, *, actor_user_id: int, category_id: int, name: str, description: str | None,
        audit_writer=AuditService.record_after_commit,
    ) -> SpuResponse:
        _fresh(session)
        with session.begin():
            merchant_id = _locked_merchant(session, actor_user_id)
            category = session.scalar(
                select(Category).where(Category.id == category_id, Category.merchant_id == merchant_id)
            )
            if category is None:
                raise CatalogNotFound("category not found")
            row = Spu(merchant_id=merchant_id, category_id=category_id, name=name, description=description)
            session.add(row)
            session.flush()
            session.refresh(row)
            response = _spu_response(row)
            target_id = row.id
        audit_writer(
            actor_user_id=actor_user_id, event_type="catalog_spu_created", target_type="spu", target_id=target_id,
            outcome="CREATED", event_data={"merchant_id": merchant_id, "category_id": category_id},
        )
        return response

    @staticmethod
    def _owned_spu(session: Session, *, spu_id: int, merchant_id: int, lock: bool = False) -> Spu:
        query = select(Spu).where(Spu.id == spu_id, Spu.merchant_id == merchant_id)
        if lock:
            query = query.with_for_update().execution_options(populate_existing=True)
        row = session.scalar(query)
        if row is None:
            raise CatalogNotFound("SPU not found")
        return row

    @classmethod
    def get_spu(cls, session: Session, *, identity, spu_id: int) -> SpuDetail:
        merchant_id = _tenant(identity, write=False)
        row = cls._owned_spu(session, spu_id=spu_id, merchant_id=merchant_id)
        skus = session.scalars(select(Sku).where(Sku.spu_id == spu_id).order_by(Sku.id)).all()
        return SpuDetail(**_spu_response(row).model_dump(), skus=[_sku_response(sku) for sku in skus])

    @classmethod
    def update_spu(
        cls, session: Session, *, actor_user_id: int, spu_id: int, changes: dict,
        audit_writer=AuditService.record_after_commit,
    ) -> SpuResponse:
        if not changes or not set(changes) <= {"category_id", "name", "description"}:
            raise CatalogConflict("unsupported or empty SPU update")
        if any(changes[key] is None for key in ("category_id", "name") if key in changes):
            raise CatalogConflict("category_id and name cannot be null")
        _fresh(session)
        with session.begin():
            merchant_id = _locked_merchant(session, actor_user_id)
            row = cls._owned_spu(session, spu_id=spu_id, merchant_id=merchant_id, lock=True)
            if "category_id" in changes:
                category = session.scalar(
                    select(Category.id).where(
                        Category.id == changes["category_id"], Category.merchant_id == merchant_id
                    )
                )
                if category is None:
                    raise CatalogNotFound("category not found")
            for key, value in changes.items():
                setattr(row, key, value)
            session.flush()
            session.refresh(row)
            response = _spu_response(row)
            target_id = row.id
        audit_writer(
            actor_user_id=actor_user_id, event_type="catalog_spu_updated", target_type="spu", target_id=target_id,
            outcome="UPDATED", event_data={"merchant_id": merchant_id},
        )
        return response

    @classmethod
    def publish_spu(
        cls, session: Session, *, actor_user_id: int, spu_id: int,
        audit_writer=AuditService.record_after_commit,
    ) -> SpuResponse:
        _fresh(session)
        with session.begin():
            merchant_id = _locked_merchant(session, actor_user_id)
            row = cls._owned_spu(session, spu_id=spu_id, merchant_id=merchant_id, lock=True)
            if row.status == SpuStatus.ON_SALE.value:
                raise CatalogConflict("SPU is already on sale")
            sku_count = session.scalar(select(func.count(Sku.id)).where(Sku.spu_id == spu_id)) or 0
            if sku_count < 1:
                raise CatalogConflict("at least one SKU is required before publishing")
            row.status = SpuStatus.ON_SALE.value
            session.flush()
            session.refresh(row)
            response = _spu_response(row)
            target_id = row.id
        audit_writer(
            actor_user_id=actor_user_id, event_type="catalog_spu_published", target_type="spu", target_id=target_id,
            outcome="ON_SALE", event_data={"merchant_id": merchant_id},
        )
        return response

    @staticmethod
    def create_sku(
        session: Session, *, actor_user_id: int, spu_id: int, sku_code: str, specification: str, price: Decimal,
        audit_writer=AuditService.record_after_commit,
    ) -> SkuResponse:
        _fresh(session)
        try:
            with session.begin():
                merchant_id = _locked_merchant(session, actor_user_id)
                warehouse = session.scalar(
                    select(Warehouse).where(Warehouse.merchant_id == merchant_id).with_for_update()
                    .execution_options(populate_existing=True)
                )
                if warehouse is None:
                    raise CatalogConflict("merchant warehouse is not configured")
                spu = CatalogService._owned_spu(session, spu_id=spu_id, merchant_id=merchant_id, lock=True)
                row = Sku(
                    merchant_id=merchant_id, spu_id=spu.id, sku_code=sku_code,
                    specification=specification, price=price,
                )
                session.add(row)
                session.flush()
                session.add(Stock(
                    merchant_id=merchant_id, warehouse_id=warehouse.id, sku_id=row.id, quantity=0,
                ))
                session.flush()
                session.refresh(row)
                response = _sku_response(row)
                target_id = row.id
        except IntegrityError as exc:
            if _duplicate(exc):
                raise CatalogConflict("SKU code already exists for this SPU") from exc
            raise
        audit_writer(
            actor_user_id=actor_user_id, event_type="catalog_sku_created", target_type="sku", target_id=target_id,
            outcome="CREATED", event_data={"spu_id": spu_id},
        )
        return response

    @staticmethod
    def update_sku(
        session: Session, *, actor_user_id: int, sku_id: int, changes: dict,
        audit_writer=AuditService.record_after_commit,
    ) -> SkuResponse:
        if not changes or not set(changes) <= {"sku_code", "specification", "price"}:
            raise CatalogConflict("unsupported or empty SKU update")
        if any(value is None for value in changes.values()):
            raise CatalogConflict("SKU fields cannot be null")
        _fresh(session)
        try:
            with session.begin():
                merchant_id = _locked_merchant(session, actor_user_id)
                # The user lock comes first so any identity reads after a wait use
                # the current transaction snapshot; SKU's immutable parent is next.
                spu_id = session.scalar(select(Sku.spu_id).where(Sku.id == sku_id))
                if spu_id is None:
                    raise CatalogNotFound("SKU not found")
                CatalogService._owned_spu(session, spu_id=spu_id, merchant_id=merchant_id, lock=True)
                row = session.scalar(
                    select(Sku).where(Sku.id == sku_id, Sku.spu_id == spu_id)
                    .with_for_update().execution_options(populate_existing=True)
                )
                if row is None:
                    raise CatalogNotFound("SKU not found")
                for key, value in changes.items():
                    setattr(row, key, value)
                session.flush()
                session.refresh(row)
                response = _sku_response(row)
                target_id, spu_id = row.id, row.spu_id
        except IntegrityError as exc:
            if _duplicate(exc):
                raise CatalogConflict("SKU code already exists for this SPU") from exc
            raise
        audit_writer(
            actor_user_id=actor_user_id, event_type="catalog_sku_updated", target_type="sku", target_id=target_id,
            outcome="UPDATED", event_data={"spu_id": spu_id},
        )
        return response
