from uuid import UUID, uuid4
import logging

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.modules.employees.models import ShopEmployee, ShopEmployeeRole
from app.modules.merchants.models import Merchant
from app.modules.shops.models import Shop
from app.modules.users.models import User
from app.services.audit import AuditService
from app.services.identity import IdentityConflict, IdentityEligibilityError, IdentityService, Role

logger = logging.getLogger(__name__)


class OwnerTransferNotFound(Exception):
    pass


class OwnerTransferForbidden(Exception):
    pass


class OwnerTransferConflict(Exception):
    pass


def _lock_users(session: Session, ids: set[int]) -> dict[int, User]:
    rows = session.scalars(select(User).where(User.id.in_(sorted(ids))).order_by(User.id).with_for_update()
                           .execution_options(populate_existing=True)).all()
    result = {int(row.id): row for row in rows}
    if result.keys() != ids:
        raise OwnerTransferConflict("one or more users no longer exist")
    return result


def _check_target(session: Session, user: User, shop_id: int) -> int | None:
    if not user.is_active:
        raise OwnerTransferConflict("target user must be active")
    try:
        identity = IdentityService.get_identity(session, user)
    except (IdentityConflict, IdentityEligibilityError) as exc:
        raise OwnerTransferConflict("target user has an inactive or conflicting identity") from exc
    if identity.role is Role.NONE:
        return None
    if identity.role is Role.SHOP_EMPLOYEE and identity.shop_id == shop_id:
        employee = session.scalar(select(ShopEmployee).where(
            ShopEmployee.shop_id == shop_id, ShopEmployee.user_id == user.id
        ).with_for_update().execution_options(populate_existing=True))
        if employee is None:
            raise OwnerTransferConflict("target employee relationship changed")
        return int(employee.role_id)
    raise OwnerTransferConflict("target must have no identity or be an employee of this shop")


def _current_owner_identity(session: Session, owner: User, shop_id: int, *, allow_inactive: bool = False) -> None:
    if not owner.is_active and not allow_inactive:
        raise OwnerTransferConflict("current owner must be active")
    if owner.is_active:
        try:
            identity = IdentityService.get_identity(session, owner)
        except (IdentityConflict, IdentityEligibilityError) as exc:
            raise OwnerTransferConflict("current owner identity is no longer valid") from exc
        if identity.role is not Role.SHOP_OWNER or identity.shop_id != shop_id:
            raise OwnerTransferConflict("current owner identity changed")
        return
    # IdentityService deliberately hides disabled users. A force transfer may remove
    # a disabled current owner, but it still rejects any other surviving identity.
    from app.modules.employees.models import ShopEmployee
    from app.modules.merchants.models import Merchant
    from app.modules.shops.models import Shop as ShopModel
    has_other = bool(owner.is_platform_admin) or session.scalar(select(Merchant.id).where(
        Merchant.admin_user_id == owner.id
    )) is not None or session.scalar(select(ShopModel.id).where(
        ShopModel.owner_user_id == owner.id, ShopModel.id != shop_id
    )) is not None or session.scalar(select(ShopEmployee.id).where(
        ShopEmployee.user_id == owner.id
    )) is not None
    if has_other:
        raise OwnerTransferConflict("disabled current owner has another conflicting identity")


def _invalidate_ineligible_consent(session: Session, *, shop_id: int, owner_id: int,
                                  proposal_id: UUID | None) -> None:
    """Persistently consume consent once the locked current owner is no longer eligible."""
    if proposal_id is None:
        return
    with session.begin():
        users = _lock_users(session, {owner_id})
        shop = session.scalar(select(Shop).where(Shop.id == shop_id).with_for_update()
                              .execution_options(populate_existing=True))
        if (shop is None or shop.owner_user_id != owner_id
                or shop.pending_owner_proposal_id != str(proposal_id)
                or shop.pending_owner_from_user_id != owner_id):
            return
        try:
            _current_owner_identity(session, users[owner_id], shop_id)
        except OwnerTransferConflict:
            shop.pending_owner_proposal_id = None
            shop.pending_owner_from_user_id = None
            shop.pending_owner_target_user_id = None
            session.flush()


class OwnerTransferService:
    @staticmethod
    def prepare(session: Session, shop_id: int, *, proposal_id: UUID | None = None):
        row = session.execute(
            select(Shop.owner_user_id, Shop.merchant_id, Merchant.admin_user_id,
                   Shop.pending_owner_proposal_id, Shop.pending_owner_from_user_id,
                   Shop.pending_owner_target_user_id)
            .join(Merchant, Merchant.id == Shop.merchant_id).where(Shop.id == shop_id)
        ).one_or_none()
        if row is None:
            raise OwnerTransferNotFound("shop not found")
        target = row.pending_owner_target_user_id
        from_owner = row.pending_owner_from_user_id
        if proposal_id is not None and row.pending_owner_proposal_id != str(proposal_id):
            target = None
            from_owner = None
        return int(row.owner_user_id), int(row.merchant_id), int(row.admin_user_id), (
            int(from_owner) if from_owner is not None else None,
            int(target) if target is not None else None,
        )

    @staticmethod
    def end_read_only_transaction(session: Session) -> None:
        if session.new or session.dirty or session.deleted:
            raise RuntimeError("cannot discard a Session containing pending changes")
        if session.in_transaction():
            session.rollback()

    @staticmethod
    def execute(session: Session, *, actor_user_id: int, shop_id: int, expected_owner_id: int,
                expected_merchant_id: int, expected_admin_id: int, expected_proposal_ids: tuple[int | None, int | None],
                action: str, target_user_id: int | None = None, proposal_id: UUID | None = None,
                reason: str | None = None, audit_writer=AuditService.record_after_commit):
        if session.in_transaction():
            raise RuntimeError("owner transfer write requires a fresh transaction")
        expected_from, expected_target = expected_proposal_ids
        if action == "PROPOSE":
            user_ids = {actor_user_id, expected_owner_id, int(target_user_id)}
        elif action == "CONFIRM":
            user_ids = {actor_user_id, expected_owner_id, expected_admin_id}
            if expected_from is not None:
                user_ids.add(expected_from)
            if expected_target is not None:
                user_ids.add(expected_target)
        else:
            user_ids = {actor_user_id, expected_owner_id, expected_admin_id, int(target_user_id)}
        try:
            with session.begin():
                users = _lock_users(session, user_ids)
                shop = session.scalar(select(Shop).where(Shop.id == shop_id).with_for_update()
                                      .execution_options(populate_existing=True))
                if shop is None:
                    raise OwnerTransferNotFound("shop not found")
                if (shop.owner_user_id, shop.merchant_id) != (expected_owner_id, expected_merchant_id):
                    raise OwnerTransferConflict("shop ownership or merchant changed; retry")
                actor = users[actor_user_id]
                if not actor.is_active:
                    raise OwnerTransferForbidden("active authenticated user required")
                try:
                    actor_identity = IdentityService.get_identity(session, actor)
                except (IdentityConflict, IdentityEligibilityError) as exc:
                    raise OwnerTransferConflict("actor identity changed or is conflicting") from exc
                if action == "PROPOSE":
                    current_owner = users[expected_owner_id]
                    _current_owner_identity(session, current_owner, shop_id)
                    if actor_user_id != expected_owner_id or actor_identity.role is not Role.SHOP_OWNER or actor_identity.shop_id != shop_id:
                        raise OwnerTransferForbidden("only the current shop owner can propose a transfer")
                    if int(target_user_id) == expected_owner_id:
                        raise OwnerTransferConflict("owner cannot propose themselves")
                    target = users[int(target_user_id)]
                    _check_target(session, target, shop_id)
                    generated = uuid4()
                    shop.pending_owner_proposal_id = str(generated)
                    shop.pending_owner_from_user_id = expected_owner_id
                    shop.pending_owner_target_user_id = int(target_user_id)
                    session.flush()
                    response = {"shop_id": shop_id, "owner_user_id": expected_owner_id,
                                "status": "PROPOSED", "proposal_id": generated,
                                "target_user_id": int(target_user_id)}
                    audit = ("shop_owner_transfer_proposed", "PROPOSED",
                             {"from_owner_user_id": expected_owner_id, "target_user_id": int(target_user_id)})
                else:
                    if actor_identity.role is not Role.MERCHANT_ADMIN or actor_identity.merchant_id != expected_merchant_id:
                        raise OwnerTransferForbidden("current merchant administrator required")
                    merchant = session.scalar(select(Merchant).where(Merchant.id == expected_merchant_id)
                                              .with_for_update().execution_options(populate_existing=True))
                    if merchant is None or merchant.admin_user_id != actor_user_id or actor_user_id != expected_admin_id:
                        raise OwnerTransferForbidden("current merchant administrator required")
                    old_owner = users[expected_owner_id]
                    _current_owner_identity(session, old_owner, shop_id, allow_inactive=action == "FORCE")
                    if action == "CONFIRM":
                        if (proposal_id is None or shop.pending_owner_proposal_id != str(proposal_id)
                                or shop.pending_owner_from_user_id != expected_owner_id
                                or shop.pending_owner_target_user_id is None
                                or expected_from != expected_owner_id or expected_target != shop.pending_owner_target_user_id):
                            raise OwnerTransferConflict("proposal is stale, unknown, or no longer bound to this owner")
                        new_owner_id = int(shop.pending_owner_target_user_id)
                    else:
                        new_owner_id = int(target_user_id)
                    if new_owner_id == expected_owner_id or new_owner_id not in users:
                        raise OwnerTransferConflict("invalid target user")
                    target = users[new_owner_id]
                    restore_role_id = _check_target(session, target, shop_id)
                    target_employee = session.scalar(select(ShopEmployee).where(
                        ShopEmployee.shop_id == shop_id, ShopEmployee.user_id == new_owner_id
                    ).with_for_update().execution_options(populate_existing=True))
                    if restore_role_id is not None:
                        if target_employee is None:
                            raise OwnerTransferConflict("target employee relationship changed")
                        session.delete(target_employee)
                    elif target_employee is not None:
                        raise OwnerTransferConflict("target identity relationship changed")
                    outgoing_restore_role = shop.owner_restore_role_id
                    shop.owner_user_id = new_owner_id
                    shop.owner_restore_role_id = restore_role_id
                    if action == "CONFIRM" and outgoing_restore_role is not None:
                        # The composite FK guarantees this is the exact role for this shop.
                        session.add(ShopEmployee(shop_id=shop_id, user_id=expected_owner_id,
                                                 role_id=outgoing_restore_role))
                    elif action == "FORCE":
                        # Forced removal deliberately discards saved membership to avoid regranting shop access.
                        shop.owner_restore_role_id = restore_role_id
                    shop.last_owner_transfer_kind = "NORMAL" if action == "CONFIRM" else "FORCE"
                    shop.last_owner_transfer_outcome = "COMPLETED" if action == "CONFIRM" else "FORCED"
                    shop.last_owner_transfer_reason = reason if action == "FORCE" else None
                    shop.last_owner_transfer_from_user_id = expected_owner_id
                    shop.last_owner_transfer_to_user_id = new_owner_id
                    shop.last_owner_transfer_at = None  # set by database below
                    from sqlalchemy import func
                    shop.last_owner_transfer_at = func.current_timestamp()
                    shop.pending_owner_proposal_id = None
                    shop.pending_owner_from_user_id = None
                    shop.pending_owner_target_user_id = None
                    session.flush()
                    response = {"shop_id": shop_id, "owner_user_id": new_owner_id,
                                "status": "CONFIRMED" if action == "CONFIRM" else "FORCED",
                                "proposal_id": None, "target_user_id": new_owner_id}
                    audit = (("shop_owner_transfer_confirmed", "CONFIRMED", {"from_owner_user_id": expected_owner_id,
                              "to_owner_user_id": new_owner_id}) if action == "CONFIRM" else
                             ("shop_owner_transfer_forced", "FORCED", {"from_owner_user_id": expected_owner_id,
                              "to_owner_user_id": new_owner_id}))
        except IntegrityError as exc:
            raise OwnerTransferConflict("owner transfer conflicts with a database identity constraint") from exc
        except OwnerTransferConflict:
            if action == "CONFIRM":
                _invalidate_ineligible_consent(session, shop_id=shop_id, owner_id=expected_owner_id,
                                               proposal_id=proposal_id)
            raise
        try:
            audit_writer(actor_user_id=actor_user_id, event_type=audit[0], target_type="shop", target_id=shop_id,
                         outcome=audit[1], event_data=audit[2])
        except Exception as exc:
            logger.error("owner transfer audit failed event_type=%s target_id=%s error_type=%s",
                         audit[0], shop_id, type(exc).__name__)
        return response
