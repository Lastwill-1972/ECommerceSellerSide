import logging

from sqlalchemy.orm import Session

from app.db.session import get_session_factory
from app.modules.merchants.models import AuditEvent

logger = logging.getLogger(__name__)


class AuditService:
    _EVENT_FIELDS = {
        "merchant_application_submitted": ("merchant_application", frozenset()),
        "merchant_application_reviewed": ("merchant_application", frozenset({"decision"})),
        "shop_application_submitted": ("shop_application", frozenset()),
        "shop_application_reviewed": ("shop_application", frozenset({"decision"})),
        "employee_application_submitted": ("employee_application", frozenset()),
        "employee_application_reviewed": ("employee_application", frozenset({"decision"})),
        "shop_employee_removed": ("shop_employee", frozenset({"shop_id", "user_id"})),
        "shop_employee_role_created": ("shop_employee_role", frozenset({"shop_id"})),
        "shop_employee_role_changed": ("shop_employee", frozenset({"shop_id", "role_id"})),
        "catalog_category_created": ("category", frozenset({"merchant_id"})),
        "catalog_spu_created": ("spu", frozenset({"merchant_id", "category_id"})),
        "catalog_spu_updated": ("spu", frozenset({"merchant_id"})),
        "catalog_spu_published": ("spu", frozenset({"merchant_id"})),
        "catalog_sku_created": ("sku", frozenset({"spu_id"})),
        "catalog_sku_updated": ("sku", frozenset({"spu_id"})),
        "catalog_offshelf_application_submitted": (
            "product_offshelf_application", frozenset({"spu_id", "applicant_user_id"})
        ),
        "catalog_offshelf_application_reviewed": (
            "product_offshelf_application", frozenset({"spu_id", "reviewer_user_id", "decision"})
        ),
        "warehouse_access_application_submitted": (
            "warehouse_access_application", frozenset({"shop_id", "warehouse_id", "applicant_user_id"})
        ),
        "warehouse_access_application_reviewed": (
            "warehouse_access_application", frozenset({"shop_id", "warehouse_id", "reviewer_user_id", "decision"})
        ),
        "inventory_stock_adjusted": (
            "stock_movement", frozenset({"merchant_id", "warehouse_id", "sku_id", "movement_id"})
        ),
        "order_created": ("order", frozenset({"merchant_id", "shop_id", "order_id", "actor_user_id"})),
        "order_completed": ("order", frozenset({"merchant_id", "shop_id", "order_id", "actor_user_id"})),
        "order_cancelled": ("order", frozenset({"merchant_id", "shop_id", "order_id", "actor_user_id"})),
        "merchant_profile_updated": ("merchant", frozenset()),
        "shop_owner_transfer_proposed": ("shop", frozenset({"from_owner_user_id", "target_user_id"})),
        "shop_owner_transfer_confirmed": ("shop", frozenset({"from_owner_user_id", "to_owner_user_id"})),
        "shop_owner_transfer_forced": ("shop", frozenset({"from_owner_user_id", "to_owner_user_id"})),
    }

    @staticmethod
    def record_after_commit(
        *,
        actor_user_id: int,
        event_type: str,
        target_type: str,
        target_id: int,
        outcome: str,
        event_data: dict | None = None,
        session_factory=None,
    ) -> bool:
        """Write an audit row in a separate transaction after business commit.

        This is intentionally not atomic with the business operation. Failures are
        logged without exception text or request data, and must not change a
        successful business response.
        """
        target_spec = AuditService._EVENT_FIELDS.get(event_type)
        if target_spec is None or target_type != target_spec[0]:
            logger.error("audit write rejected event_type=%s target_type=%s target_id=%s", event_type, target_type, target_id)
            return False
        safe_data = event_data or {}
        valid_context = isinstance(safe_data, dict) and safe_data.keys() <= target_spec[1]
        if event_type in {
            "merchant_application_submitted", "shop_application_submitted", "employee_application_submitted"
        }:
            valid_context = valid_context and safe_data == {} and outcome == "PENDING"
        elif event_type in {
            "merchant_application_reviewed", "shop_application_reviewed", "employee_application_reviewed"
        }:
            decision = safe_data.get("decision") if isinstance(safe_data, dict) else None
            valid_context = (
                valid_context
                and set(safe_data) == {"decision"}
                and isinstance(decision, str)
                and decision in {"APPROVE", "REJECT"}
                and outcome == ("APPROVED" if decision == "APPROVE" else "REJECTED")
            )
        elif event_type in {"catalog_offshelf_application_submitted", "catalog_offshelf_application_reviewed"}:
            expected = {
                "catalog_offshelf_application_submitted": ("PENDING", {"spu_id", "applicant_user_id"}),
                "catalog_offshelf_application_reviewed": None,
            }.get(event_type)
            if event_type == "catalog_offshelf_application_reviewed":
                decision = safe_data.get("decision") if isinstance(safe_data, dict) else None
                valid_context = (
                    valid_context
                    and type(actor_user_id) is int and actor_user_id > 0
                    and type(target_id) is int and target_id > 0
                    and set(safe_data) == {"spu_id", "reviewer_user_id", "decision"}
                    and all(type(safe_data.get(key)) is int and safe_data[key] > 0 for key in ("spu_id", "reviewer_user_id"))
                    and safe_data.get("reviewer_user_id") == actor_user_id
                    and isinstance(decision, str)
                    and decision in {"APPROVE", "REJECT"}
                    and outcome == ("APPROVED" if decision == "APPROVE" else "REJECTED")
                )
            else:
                valid_context = (
                    valid_context
                    and type(actor_user_id) is int and actor_user_id > 0
                    and type(target_id) is int and target_id > 0
                    and safe_data.get("applicant_user_id") == actor_user_id
                    and outcome == expected[0]
                    and set(safe_data) == expected[1]
                    and all(type(value) is int and value > 0 for value in safe_data.values())
                )
        elif event_type in {"warehouse_access_application_submitted", "warehouse_access_application_reviewed"}:
            if event_type.endswith("submitted"):
                valid_context = (
                    valid_context
                    and type(actor_user_id) is int and actor_user_id > 0
                    and type(target_id) is int and target_id > 0
                    and set(safe_data) == {"shop_id", "warehouse_id", "applicant_user_id"}
                    and all(type(safe_data.get(key)) is int and safe_data[key] > 0
                            for key in ("shop_id", "warehouse_id", "applicant_user_id"))
                    and safe_data.get("applicant_user_id") == actor_user_id
                    and outcome == "PENDING"
                )
            else:
                decision = safe_data.get("decision") if isinstance(safe_data, dict) else None
                valid_context = (
                    valid_context
                    and type(actor_user_id) is int and actor_user_id > 0
                    and type(target_id) is int and target_id > 0
                    and set(safe_data) == {"shop_id", "warehouse_id", "reviewer_user_id", "decision"}
                    and all(type(safe_data.get(key)) is int and safe_data[key] > 0
                            for key in ("shop_id", "warehouse_id", "reviewer_user_id"))
                    and safe_data.get("reviewer_user_id") == actor_user_id
                    and isinstance(decision, str)
                    and decision in {"APPROVE", "REJECT"}
                    and outcome == ("APPROVED" if decision == "APPROVE" else "REJECTED")
                )
        elif event_type == "inventory_stock_adjusted":
            valid_context = (
                valid_context
                and type(actor_user_id) is int and actor_user_id > 0
                and type(target_id) is int and target_id > 0
                and set(safe_data) == {"merchant_id", "warehouse_id", "sku_id", "movement_id"}
                and all(type(safe_data.get(key)) is int and safe_data[key] > 0
                        for key in ("merchant_id", "warehouse_id", "sku_id", "movement_id"))
                and safe_data.get("movement_id") == target_id
                and outcome == "ADJUSTED"
            )
        elif event_type in {"order_created", "order_completed", "order_cancelled"}:
            expected_outcome = {
                "order_created": "PENDING",
                "order_completed": "COMPLETED",
                "order_cancelled": "CANCELLED",
            }[event_type]
            valid_context = (
                valid_context
                and type(actor_user_id) is int and actor_user_id > 0
                and type(target_id) is int and target_id > 0
                and set(safe_data) == {"merchant_id", "shop_id", "order_id", "actor_user_id"}
                and all(type(safe_data.get(key)) is int and safe_data[key] > 0
                        for key in ("merchant_id", "shop_id", "order_id", "actor_user_id"))
                and safe_data["order_id"] == target_id
                and safe_data["actor_user_id"] == actor_user_id
                and outcome == expected_outcome
            )
        elif event_type.startswith("shop_owner_transfer_"):
            expected = {
                "shop_owner_transfer_proposed": "PROPOSED",
                "shop_owner_transfer_confirmed": "CONFIRMED",
                "shop_owner_transfer_forced": "FORCED",
            }[event_type]
            valid_context = (
                valid_context and type(actor_user_id) is int and actor_user_id > 0
                and type(target_id) is int and target_id > 0
                and set(safe_data) == target_spec[1]
                and all(type(value) is int and value > 0 for value in safe_data.values())
                and outcome == expected
            )
        elif event_type == "merchant_profile_updated":
            valid_context = valid_context and type(actor_user_id) is int and actor_user_id > 0 \
                and type(target_id) is int and target_id > 0 and safe_data == {} and outcome == "UPDATED"
        elif event_type.startswith("catalog_"):
            expected = {
                "catalog_category_created": ("CREATED", {"merchant_id"}),
                "catalog_spu_created": ("CREATED", {"merchant_id", "category_id"}),
                "catalog_spu_updated": ("UPDATED", {"merchant_id"}),
                "catalog_spu_published": ("ON_SALE", {"merchant_id"}),
                "catalog_sku_created": ("CREATED", {"spu_id"}),
                "catalog_sku_updated": ("UPDATED", {"spu_id"}),
            }.get(event_type)
            valid_context = (
                valid_context
                and expected is not None
                and outcome == expected[0]
                and set(safe_data) == expected[1]
                and all(type(value) is int and value > 0 for value in safe_data.values())
            )
        else:
            expected = {
                "shop_employee_removed": ("REMOVED", {"shop_id", "user_id"}),
                "shop_employee_role_created": ("CREATED", {"shop_id"}),
                "shop_employee_role_changed": ("UPDATED", {"shop_id", "role_id"}),
            }.get(event_type)
            valid_context = (
                valid_context
                and expected is not None
                and outcome == expected[0]
                and set(safe_data) == expected[1]
                and all(type(value) is int and value > 0 for value in safe_data.values())
            )
        if not valid_context:
            logger.error("audit write rejected event_type=%s target_type=%s target_id=%s", event_type, target_type, target_id)
            return False
        try:
            factory = session_factory or get_session_factory()
            with factory() as audit_session:
                with audit_session.begin():
                    audit_session.add(
                        AuditEvent(
                            actor_user_id=actor_user_id,
                            event_type=event_type,
                            target_type=target_type,
                            target_id=target_id,
                            outcome=outcome,
                            event_data=safe_data,
                        )
                    )
            return True
        except Exception as exc:
            logger.error(
                "audit write failed event_type=%s target_type=%s target_id=%s error_type=%s",
                event_type,
                target_type,
                target_id,
                type(exc).__name__,
            )
            return False
