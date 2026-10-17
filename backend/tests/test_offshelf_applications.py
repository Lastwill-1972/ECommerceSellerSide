from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import logging
import secrets
from threading import Barrier
from threading import Event
import time

import pytest
import jwt
from sqlalchemy import func, select, text

from app.modules.catalog.models import ProductOffshelfApplication, Spu, Sku
from app.modules.inventory.models import Warehouse
from app.modules.merchants.models import AuditEvent, Merchant
from app.modules.shops.models import Shop
from app.modules.employees.models import ShopEmployee, ShopEmployeeRole
from app.modules.users.models import User
from app.core.config import get_settings


def _account(client, label):
    suffix = secrets.token_hex(5)
    username = f"offshelf-{label}-{suffix}"
    response = client.post(
        "/api/v1/auth/register",
        json={"username": username, "email": f"{username}@example.com", "password": "long-correct-password"},
    )
    assert response.status_code == 201, response.text
    user_id = response.json()["id"]
    token = client.post(
        "/api/v1/auth/login", json={"identifier": username, "password": "long-correct-password"}
    ).json()["access_token"]
    return user_id, {"Authorization": f"Bearer {token}"}


def _make_product(client, factory, admin_id, headers, *, label="product"):
    with factory.begin() as session:
        merchant = Merchant(name=f"Offshelf {label} {secrets.token_hex(4)}", admin_user_id=admin_id)
        session.add(merchant)
        session.flush()
        merchant_id = merchant.id
        session.add(Warehouse(merchant_id=merchant_id, name="Shared warehouse"))
    category = client.post("/api/v1/catalog/categories", headers=headers, json={"name": f"Category {secrets.token_hex(4)}"})
    assert category.status_code == 201, category.text
    spu_response = client.post(
        "/api/v1/catalog/spus", headers=headers,
        json={"category_id": category.json()["id"], "name": f"Product {label} {secrets.token_hex(4)}"},
    )
    assert spu_response.status_code == 201, spu_response.text
    spu_id = spu_response.json()["id"]
    sku_response = client.post(
        f"/api/v1/catalog/spus/{spu_id}/skus", headers=headers,
        json={"sku_code": "ONE", "specification": "test", "price": "12.30"},
    )
    assert sku_response.status_code == 201, sku_response.text
    publish = client.patch(f"/api/v1/catalog/spus/{spu_id}/publish", headers=headers)
    assert publish.status_code == 200, publish.text
    return merchant_id, spu_id, sku_response.json()["id"]


def _platform(factory, client, label):
    user_id, headers = _account(client, label)
    with factory.begin() as session:
        session.get(User, user_id).is_platform_admin = True
    return user_id, headers


def _assign_shop_roles(factory, merchant_id, owner_user_id, employee_user_id):
    with factory.begin() as session:
        shop = Shop(
            merchant_id=merchant_id, name=f"Offshelf shop {secrets.token_hex(4)}", owner_user_id=owner_user_id,
        )
        session.add(shop)
        session.flush()
        role = ShopEmployeeRole(shop_id=shop.id, name="普通员工")
        session.add(role)
        session.flush()
        session.add(ShopEmployee(shop_id=shop.id, user_id=employee_user_id, role_id=role.id))
        return shop.id


def test_offshelf_api_scope_validation_review_and_preserved_history(auth_client):
    client, factory = auth_client
    merchant_admin_id, merchant_headers = _account(client, "merchant")
    other_admin_id, other_headers = _account(client, "other-merchant")
    owner_id, owner_headers = _account(client, "owner")
    employee_id, employee_headers = _account(client, "employee")
    none_id, none_headers = _account(client, "none")
    platform_id, platform_headers = _platform(factory, client, "platform")
    merchant_id, spu_id, sku_id = _make_product(client, factory, merchant_admin_id, merchant_headers)
    shop_id = _assign_shop_roles(factory, merchant_id, owner_id, employee_id)
    _, other_spu_id, _ = _make_product(client, factory, other_admin_id, other_headers, label="other")

    application = client.post("/api/v1/catalog/offshelf-applications", headers=merchant_headers, json={"spu_id": spu_id})
    assert application.status_code == 201, application.text
    body = application.json()
    assert body["status"] == "PENDING" and body["applicant_user_id"] == merchant_admin_id
    assert body["spu"] == {
        "id": spu_id, "name": body["spu"]["name"], "status": "ON_SALE", "merchant_id": merchant_id,
    }
    assert client.post(
        "/api/v1/catalog/offshelf-applications", headers=merchant_headers, json={"spu_id": spu_id, "merchant_id": merchant_id}
    ).status_code == 422
    assert client.post(
        "/api/v1/catalog/offshelf-applications", headers=other_headers, json={"spu_id": spu_id}
    ).status_code == 404
    assert client.post(
        "/api/v1/catalog/offshelf-applications", headers=merchant_headers, json={"spu_id": other_spu_id}
    ).status_code == 404
    assert client.post(
        "/api/v1/catalog/offshelf-applications", headers=merchant_headers, json={"spu_id": spu_id}
    ).status_code == 409
    for headers in (owner_headers, employee_headers, none_headers, platform_headers):
        assert client.post(
            "/api/v1/catalog/offshelf-applications", headers=headers, json={"spu_id": spu_id}
        ).status_code == 403
    for headers in (owner_headers, employee_headers, none_headers):
        assert client.get("/api/v1/catalog/offshelf-applications", headers=headers).status_code == 403
    assert client.get("/api/v1/auth/me", headers=owner_headers).json()["role"] == "SHOP_OWNER"
    employee_identity = client.get("/api/v1/auth/me", headers=employee_headers).json()
    assert employee_identity["role"] == "SHOP_EMPLOYEE" and employee_identity["shop_id"] == shop_id
    now = datetime.now(timezone.utc)
    spoof_token = jwt.encode(
        {"sub": str(none_id), "iat": now, "exp": now + timedelta(minutes=5), "role": "PLATFORM_ADMIN"},
        get_settings().auth_jwt_secret.get_secret_value(), algorithm="HS256",
    )
    spoof_headers = {"Authorization": f"Bearer {spoof_token}"}
    assert client.get("/api/v1/catalog/offshelf-applications", headers=spoof_headers).status_code == 403
    assert client.post(
        f"/api/v1/catalog/offshelf-applications/{body['id']}/review", headers=spoof_headers,
        json={"decision": "APPROVE"},
    ).status_code == 403
    assert client.get("/api/v1/catalog/spus", headers=platform_headers).status_code == 403
    platform_list = client.get("/api/v1/catalog/offshelf-applications", headers=platform_headers).json()
    assert platform_list["total"] == 1
    assert set(platform_list["items"][0]) == {
        "id", "spu_id", "applicant_user_id", "status", "reviewer_user_id", "rejection_reason",
        "created_at", "reviewed_at", "spu",
    }
    assert set(platform_list["items"][0]["spu"]) == {"id", "name", "status", "merchant_id"}
    assert client.get("/api/v1/catalog/offshelf-applications?status=REJECTED", headers=platform_headers).json()["total"] == 1
    assert client.get("/api/v1/catalog/offshelf-applications", headers=other_headers).json()["total"] == 0
    # ON_SALE is a prerequisite; a normal merchant may still publish again after an approval.
    categories = client.get("/api/v1/catalog/categories", headers=merchant_headers).json()["items"]
    off_shelf = client.post(
        "/api/v1/catalog/spus", headers=merchant_headers,
        json={"category_id": categories[0]["id"], "name": f"Off sale {secrets.token_hex(4)}"},
    )
    assert off_shelf.status_code == 201
    assert client.post(
        "/api/v1/catalog/offshelf-applications", headers=merchant_headers,
        json={"spu_id": off_shelf.json()["id"]},
    ).status_code == 409
    assert client.post(
        f"/api/v1/catalog/offshelf-applications/{body['id']}/review", headers=none_headers,
        json={"decision": "APPROVE"},
    ).status_code == 403
    assert client.post(
        f"/api/v1/catalog/offshelf-applications/{body['id']}/review", headers=merchant_headers,
        json={"decision": "APPROVE"},
    ).status_code == 403
    for headers in (owner_headers, employee_headers):
        assert client.post(
            f"/api/v1/catalog/offshelf-applications/{body['id']}/review", headers=headers,
            json={"decision": "APPROVE"},
        ).status_code == 403
    assert client.post(
        "/api/v1/catalog/offshelf-applications/9223372036854775000/review", headers=merchant_headers,
        json={"decision": "APPROVE"},
    ).status_code == 403
    assert client.post(
        "/api/v1/catalog/offshelf-applications/9223372036854775000/review", headers=platform_headers,
        json={"decision": "APPROVE"},
    ).status_code == 404
    assert client.post(
        f"/api/v1/catalog/offshelf-applications/{body['id']}/review", headers=platform_headers,
        json={"decision": "APPROVE", "rejection_reason": "not allowed"},
    ).status_code == 422
    assert client.post(
        f"/api/v1/catalog/offshelf-applications/{body['id']}/review", headers=platform_headers,
        json={"decision": "REJECT", "rejection_reason": "   "},
    ).status_code == 422
    rejected = client.post(
        f"/api/v1/catalog/offshelf-applications/{body['id']}/review", headers=platform_headers,
        json={"decision": "REJECT", "rejection_reason": "停售资料待补"},
    )
    assert rejected.status_code == 200, rejected.text
    assert rejected.json()["status"] == "REJECTED" and rejected.json()["spu"]["status"] == "ON_SALE"
    with factory() as session:
        review_event = session.scalar(select(AuditEvent).where(
            AuditEvent.event_type == "catalog_offshelf_application_reviewed",
            AuditEvent.target_id == body["id"],
        ))
        assert review_event is not None and review_event.actor_user_id == platform_id
        assert review_event.event_data == {"spu_id": spu_id, "reviewer_user_id": platform_id, "decision": "REJECT"}
    assert client.post(
        "/api/v1/catalog/offshelf-applications", headers=merchant_headers, json={"spu_id": spu_id}
    ).status_code == 201
    history = client.get("/api/v1/catalog/offshelf-applications?status=REJECTED", headers=merchant_headers).json()
    assert history["total"] == 1 and history["items"][0]["rejection_reason"] == "停售资料待补"
    assert client.post(
        f"/api/v1/catalog/offshelf-applications/{body['id']}/review", headers=platform_headers,
        json={"decision": "APPROVE"},
    ).status_code == 409
    assert client.get("/api/v1/catalog/offshelf-applications", headers=merchant_headers).json()["total"] == 2
    second_application_id = next(
        row["id"] for row in client.get("/api/v1/catalog/offshelf-applications", headers=merchant_headers).json()["items"]
        if row["status"] == "PENDING"
    )
    approved_again = client.post(
        f"/api/v1/catalog/offshelf-applications/{second_application_id}/review", headers=platform_headers,
        json={"decision": "APPROVE"},
    )
    assert approved_again.status_code == 200 and approved_again.json()["spu"]["status"] == "OFF_SALE"
    assert client.patch(f"/api/v1/catalog/spus/{spu_id}/publish", headers=merchant_headers).status_code == 200
    with factory.begin() as session:
        self_application = ProductOffshelfApplication(
            spu_id=other_spu_id, applicant_user_id=platform_id, status="PENDING",
        )
        session.add(self_application)
        session.flush()
        self_application_id = self_application.id
    assert client.post(
        f"/api/v1/catalog/offshelf-applications/{self_application_id}/review", headers=platform_headers,
        json={"decision": "APPROVE"},
    ).status_code == 403
    with factory.begin() as session:
        session.get(ProductOffshelfApplication, self_application_id).status = "REJECTED"
    with factory() as session:
        app_rows = session.scalars(select(ProductOffshelfApplication).where(
            ProductOffshelfApplication.spu_id == spu_id
        ).order_by(ProductOffshelfApplication.id)).all()
        assert [row.status for row in app_rows] == ["REJECTED", "APPROVED"]
        assert session.get(Spu, spu_id).status == "ON_SALE"
        assert session.get(Sku, sku_id).price == Decimal("12.30")
        assert session.scalar(select(func.count(AuditEvent.id)).where(
            AuditEvent.event_type == "catalog_offshelf_application_reviewed",
            AuditEvent.target_type == "product_offshelf_application", AuditEvent.target_id == body["id"],
        )) == 1


def test_offshelf_simultaneous_submit_and_review_only_commit_one(auth_client):
    client, factory = auth_client
    admin_id, admin_headers = _account(client, "submit-race")
    _, spu_id, _ = _make_product(client, factory, admin_id, admin_headers, label="submit-race")
    platform_a_id, platform_a_headers = _platform(factory, client, "reviewer-a")
    platform_b_id, platform_b_headers = _platform(factory, client, "reviewer-b")

    gate = Barrier(2)

    def submit():
        gate.wait()
        return client.post("/api/v1/catalog/offshelf-applications", headers=admin_headers, json={"spu_id": spu_id})

    with ThreadPoolExecutor(max_workers=2) as pool:
        submit_futures = [pool.submit(submit) for _ in range(2)]
        submit_responses = [future.result(timeout=15) for future in submit_futures]
    assert sorted(response.status_code for response in submit_responses) == [201, 409]
    application_id = next(response.json()["id"] for response in submit_responses if response.status_code == 201)
    gate = Barrier(2)

    def review(headers, decision):
        gate.wait()
        return client.post(
            f"/api/v1/catalog/offshelf-applications/{application_id}/review", headers=headers,
            json={"decision": decision, "rejection_reason": "原因"} if decision == "REJECT" else {"decision": decision},
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        review_futures = [
            pool.submit(review, platform_a_headers, "APPROVE"), pool.submit(review, platform_b_headers, "REJECT"),
        ]
        review_responses = [future.result(timeout=15) for future in review_futures]
    assert sorted(response.status_code for response in review_responses) == [200, 409]
    with factory() as session:
        application = session.get(ProductOffshelfApplication, application_id)
        spu = session.get(Spu, spu_id)
        assert application.status in {"APPROVED", "REJECTED"}
        assert (spu.status == "OFF_SALE") is (application.status == "APPROVED")
        winner = application.reviewer_user_id
        assert winner in {platform_a_id, platform_b_id}
        assert session.scalar(select(func.count(ProductOffshelfApplication.id)).where(
            ProductOffshelfApplication.spu_id == spu_id, ProductOffshelfApplication.status == "PENDING",
        )) == 0


def test_approve_racing_publish_observes_allowed_republish_semantics(auth_client):
    client, factory = auth_client
    admin_id, admin_headers = _account(client, "publish-race")
    platform_id, platform_headers = _platform(factory, client, "publish-reviewer")
    _, spu_id, _ = _make_product(client, factory, admin_id, admin_headers, label="publish-race")
    application = client.post(
        "/api/v1/catalog/offshelf-applications", headers=admin_headers, json={"spu_id": spu_id},
    ).json()
    gate = Barrier(2)

    def publish():
        gate.wait()
        return client.patch(f"/api/v1/catalog/spus/{spu_id}/publish", headers=admin_headers)

    def approve():
        gate.wait()
        return client.post(
            f"/api/v1/catalog/offshelf-applications/{application['id']}/review", headers=platform_headers,
            json={"decision": "APPROVE"},
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        publish_future = pool.submit(publish)
        review_future = pool.submit(approve)
        publish_response, review_response = publish_future.result(timeout=15), review_future.result(timeout=15)
    assert review_response.status_code == 200, review_response.text
    assert publish_response.status_code in {200, 409}, publish_response.text
    with factory() as session:
        assert session.get(ProductOffshelfApplication, application["id"]).status == "APPROVED"
        final_status = session.get(Spu, spu_id).status
        # If publish acquired the SPU first it saw ON_SALE and failed, leaving the
        # later approval OFF_SALE. If approval committed first, the existing publish
        # path is allowed to put the SPU back ON_SALE.
        assert (publish_response.status_code == 200 and final_status == "ON_SALE") or (
            publish_response.status_code == 409 and final_status == "OFF_SALE"
        )


def test_review_identity_is_rechecked_after_mysql_user_lock_wait(auth_client, monkeypatch):
    client, factory = auth_client
    admin_id, admin_headers = _account(client, "snapshot-admin")
    platform_id, platform_headers = _platform(factory, client, "snapshot-platform")
    _, spu_id, _ = _make_product(client, factory, admin_id, admin_headers, label="snapshot")
    application = client.post(
        "/api/v1/catalog/offshelf-applications", headers=admin_headers, json={"spu_id": spu_id},
    ).json()

    # The holder makes the reviewer acquire a conflicting merchant identity while
    # the review request is waiting for the same users row. The request must read
    # identity relationships after its lock wait, in its new write transaction.
    holder = factory()
    holder.execute(select(User).where(User.id == platform_id).with_for_update())
    holder.add(Merchant(name=f"New merchant identity {secrets.token_hex(4)}", admin_user_id=platform_id))
    holder.flush()
    waiting_for_user = Event()
    from app.services import offshelf_applications as service_module

    original_lock_users = service_module._lock_users

    def signal_then_lock(session, user_ids):
        if platform_id in user_ids:
            waiting_for_user.set()
        return original_lock_users(session, user_ids)

    monkeypatch.setattr(service_module, "_lock_users", signal_then_lock)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(
            client.post,
            f"/api/v1/catalog/offshelf-applications/{application['id']}/review",
            headers=platform_headers, json={"decision": "APPROVE"},
        )
        assert waiting_for_user.wait(timeout=5)
        # Confirm the worker cannot finish while the MySQL user-row lock is held.
        time.sleep(0.25)
        assert not future.done()
        holder.commit()
        holder.close()
        response = future.result(timeout=15)
    assert response.status_code == 403, response.text
    with factory() as session:
        assert session.get(ProductOffshelfApplication, application["id"]).status == "PENDING"
        assert session.get(Spu, spu_id).status == "ON_SALE"


def test_approve_requires_on_sale_flush_rolls_back_both_rows_and_audit(auth_client, monkeypatch):
    client, factory = auth_client
    admin_id, admin_headers = _account(client, "rollback-admin")
    platform_id, platform_headers = _platform(factory, client, "rollback-platform")
    _, spu_id, sku_id = _make_product(client, factory, admin_id, admin_headers, label="rollback")
    application = client.post("/api/v1/catalog/offshelf-applications", headers=admin_headers, json={"spu_id": spu_id}).json()

    # Exercise the ON_SALE recheck and prove that a failed approval leaves PENDING intact.
    with factory.begin() as session:
        session.get(Spu, spu_id).status = "OFF_SALE"
    response = client.post(
        f"/api/v1/catalog/offshelf-applications/{application['id']}/review", headers=platform_headers,
        json={"decision": "APPROVE"},
    )
    assert response.status_code == 409
    with factory() as session:
        assert session.get(ProductOffshelfApplication, application["id"]).status == "PENDING"
        assert session.get(Spu, spu_id).status == "OFF_SALE"
        assert session.get(Sku, sku_id).price == Decimal("12.30")
        assert session.scalar(select(func.count(AuditEvent.id)).where(
            AuditEvent.event_type == "catalog_offshelf_application_reviewed", AuditEvent.target_id == application["id"],
        )) == 0
    assert client.delete("/api/v1/catalog/offshelf-applications", headers=platform_headers).status_code == 405

    # Restore a valid state, then inject a failure after both approval mutations are flushed.
    with factory.begin() as session:
        session.get(Spu, spu_id).status = "ON_SALE"
    from app.services.offshelf_applications import OffshelfApplicationService
    from app.modules.catalog.offshelf_schemas import OffshelfDecision

    with factory() as session:
        original_flush = session.flush

        def flush_then_fail(*args, **kwargs):
            original_flush(*args, **kwargs)
            raise RuntimeError("injected approval flush failure")

        monkeypatch.setattr(session, "flush", flush_then_fail)
        with pytest.raises(RuntimeError, match="injected approval flush failure"):
            OffshelfApplicationService.review(
                session, reviewer_user_id=platform_id, application_id=application["id"],
                decision=OffshelfDecision.APPROVE, rejection_reason=None,
            )
    with factory() as session:
        assert session.get(ProductOffshelfApplication, application["id"]).status == "PENDING"
        assert session.get(Spu, spu_id).status == "ON_SALE"
        assert session.scalar(select(func.count(AuditEvent.id)).where(
            AuditEvent.event_type == "catalog_offshelf_application_reviewed", AuditEvent.target_id == application["id"],
        )) == 0


def test_audit_is_separate_and_failure_does_not_change_success(auth_client, monkeypatch, caplog):
    client, factory = auth_client
    admin_id, admin_headers = _account(client, "audit-admin")
    platform_id, platform_headers = _platform(factory, client, "audit-platform")
    _, spu_id, _ = _make_product(client, factory, admin_id, admin_headers, label="audit")
    caplog.set_level(logging.ERROR, logger="app.services.audit")
    app = client.post("/api/v1/catalog/offshelf-applications", headers=admin_headers, json={"spu_id": spu_id})
    assert app.status_code == 201, app.text
    with factory() as session:
        event = session.scalar(select(AuditEvent).where(
            AuditEvent.event_type == "catalog_offshelf_application_submitted", AuditEvent.target_id == app.json()["id"],
        ))
        assert event is not None and event.actor_user_id == admin_id
        assert event.event_data == {"spu_id": spu_id, "applicant_user_id": admin_id}

    class AuditFailure:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def add(self, _event):
            pass

        def begin(self):
            class Transaction:
                def __enter__(self):
                    return self

                def __exit__(self, exc_type, exc, tb):
                    if exc_type is None:
                        raise RuntimeError("private audit failure detail")
                    return False
            return Transaction()

    monkeypatch.setattr("app.services.audit.get_session_factory", lambda: lambda: AuditFailure())
    category_id = client.get("/api/v1/catalog/categories", headers=admin_headers).json()["items"][0]["id"]
    second_spu = client.post(
        "/api/v1/catalog/spus", headers=admin_headers,
        json={"category_id": category_id, "name": f"Audit failure {secrets.token_hex(4)}"},
    )
    assert second_spu.status_code == 201
    second_sku = client.post(
        f"/api/v1/catalog/spus/{second_spu.json()['id']}/skus", headers=admin_headers,
        json={"sku_code": "ONE", "specification": "test", "price": "1.00"},
    )
    assert second_sku.status_code == 201
    assert client.patch(
        f"/api/v1/catalog/spus/{second_spu.json()['id']}/publish", headers=admin_headers,
    ).status_code == 200
    failed_audit = client.post(
        "/api/v1/catalog/offshelf-applications", headers=admin_headers,
        json={"spu_id": second_spu.json()["id"]},
    )
    assert failed_audit.status_code == 201, failed_audit.text
    approved = client.post(
        f"/api/v1/catalog/offshelf-applications/{failed_audit.json()['id']}/review", headers=platform_headers,
        json={"decision": "APPROVE"},
    )
    assert approved.status_code == 200, approved.text
    assert "private audit failure detail" not in caplog.text
    assert "error_type=RuntimeError" in caplog.text
    with factory() as session:
        assert session.get(ProductOffshelfApplication, failed_audit.json()["id"]).status == "APPROVED"
        assert session.get(Spu, second_spu.json()["id"]).status == "OFF_SALE"
        assert session.get(Sku, second_sku.json()["id"]).price == Decimal("1.00")
        assert session.scalar(select(func.count(AuditEvent.id)).where(
            AuditEvent.event_type == "catalog_offshelf_application_submitted",
            AuditEvent.target_id == failed_audit.json()["id"],
        )) == 0
        assert session.scalar(select(func.count(AuditEvent.id)).where(
            AuditEvent.event_type == "catalog_offshelf_application_reviewed",
            AuditEvent.target_id == failed_audit.json()["id"],
        )) == 0


def test_offshelf_audit_rejects_malformed_context_without_opening_session(monkeypatch, caplog):
    from app.services.audit import AuditService

    caplog.set_level(logging.ERROR, logger="app.services.audit")
    monkeypatch.setattr(
        "app.services.audit.get_session_factory",
        lambda: pytest.fail("invalid audit context must be rejected before opening a session"),
    )
    invalid_events = (
        {
            "actor_user_id": 7, "event_type": "catalog_offshelf_application_submitted",
            "target_type": "product_offshelf_application", "target_id": 11, "outcome": "PENDING",
            "event_data": {"spu_id": 3, "applicant_user_id": 8},
        },
        {
            "actor_user_id": 7, "event_type": "catalog_offshelf_application_reviewed",
            "target_type": "product_offshelf_application", "target_id": 11, "outcome": "APPROVED",
            "event_data": {"spu_id": 3, "reviewer_user_id": 7, "decision": []},
        },
        {
            "actor_user_id": 7, "event_type": "catalog_offshelf_application_reviewed",
            "target_type": "product_offshelf_application", "target_id": 11, "outcome": "APPROVED",
            "event_data": {"spu_id": 3, "reviewer_user_id": 8, "decision": "APPROVE"},
        },
        {
            "actor_user_id": 7, "event_type": "catalog_offshelf_application_reviewed",
            "target_type": "product_offshelf_application", "target_id": 11, "outcome": "APPROVED",
            "event_data": {"spu_id": 3, "reviewer_user_id": 7, "decision": "APPROVE", "secret": "do-not-log"},
        },
    )
    assert all(AuditService.record_after_commit(**event) is False for event in invalid_events)
    assert "do-not-log" not in caplog.text
