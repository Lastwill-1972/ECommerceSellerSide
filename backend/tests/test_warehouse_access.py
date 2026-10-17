import secrets
import time
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Event

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError

from app.modules.employees.models import ShopEmployee, ShopEmployeeRole
from app.modules.inventory.models import Warehouse, WarehouseAccess, WarehouseAccessApplication
from app.modules.inventory.schemas import ApplicationDecision
from app.modules.merchants.models import AuditEvent, Merchant
from app.modules.shops.models import Shop
from app.modules.users.models import User
from app.services.warehouse_access import WarehouseAccessService, is_authorized


def _account(client, label):
    suffix = secrets.token_hex(5)
    username = f"warehouse-{label}-{suffix}"
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


def _merchant_shop(factory, admin_id, owner_id, *, warehouse=True, label="one"):
    with factory.begin() as session:
        merchant = Merchant(name=f"Warehouse {label} {secrets.token_hex(4)}", admin_user_id=admin_id)
        session.add(merchant)
        session.flush()
        shop = Shop(merchant_id=merchant.id, name=f"Shop {label} {secrets.token_hex(4)}", owner_user_id=owner_id)
        session.add(shop)
        if warehouse:
            session.add(Warehouse(merchant_id=merchant.id, name=f"Warehouse {label}"))
        session.flush()
        return merchant.id, shop.id


def test_access_application_scopes_empty_body_audit_and_grant(auth_client):
    client, factory = auth_client
    admin_id, admin_headers = _account(client, "admin")
    other_admin_id, other_admin_headers = _account(client, "other-admin")
    owner_id, owner_headers = _account(client, "owner")
    employee_id, employee_headers = _account(client, "employee")
    platform_id, platform_headers = _account(client, "platform")
    none_id, none_headers = _account(client, "none")
    with factory.begin() as session:
        session.get(User, platform_id).is_platform_admin = True
    merchant_id, shop_id = _merchant_shop(factory, admin_id, owner_id)
    other_merchant_id, _other_shop_id = _merchant_shop(factory, other_admin_id, _account(client, "other-owner")[0], label="other")
    with factory.begin() as session:
        role = ShopEmployeeRole(shop_id=shop_id, name="普通员工")
        session.add(role)
        session.flush()
        session.add(ShopEmployee(shop_id=shop_id, user_id=employee_id, role_id=role.id))

    path = "/api/v1/inventory/access-applications"
    assert client.post(path, headers=owner_headers).status_code == 201
    body = client.get(path, headers=owner_headers).json()["items"][0]
    assert set(body) == {"id", "shop_id", "applicant_user_id", "status", "reviewer_user_id", "rejection_reason", "created_at", "reviewed_at"}
    assert body["shop_id"] == shop_id and body["applicant_user_id"] == owner_id and body["status"] == "PENDING"
    assert client.post(path, headers=owner_headers, json={}).status_code == 409
    assert client.post(path, headers=owner_headers, json={"shop_id": shop_id}).status_code == 422
    for headers in (employee_headers, platform_headers, none_headers):
        assert client.post(path, headers=headers).status_code == 403
        assert client.get(path, headers=headers).status_code == 403
    assert client.get(path, headers=admin_headers).json()["total"] == 1
    assert client.get(path, headers=other_admin_headers).json()["total"] == 0
    assert client.get(path, headers=admin_headers, params={"merchant_id": other_merchant_id}).json()["total"] == 1
    assert client.get(path, headers=owner_headers, params={"shop_id": 999999999}).json()["total"] == 1
    application_id = body["id"]

    assert client.post(f"{path}/{application_id}/review", headers=owner_headers, json={"decision": "APPROVE"}).status_code == 403
    assert client.post(f"{path}/9223372036854775000/review", headers=none_headers, json={"decision": "APPROVE"}).status_code == 403
    assert client.post(f"{path}/{application_id}/review", headers=other_admin_headers, json={"decision": "APPROVE"}).status_code == 404
    assert client.post(f"{path}/9223372036854775000/review", headers=admin_headers, json={"decision": "APPROVE"}).status_code == 404
    assert client.post(f"{path}/{application_id}/review", headers=admin_headers, json={"decision": "APPROVE", "rejection_reason": "bad"}).status_code == 422
    assert client.post(f"{path}/{application_id}/review", headers=admin_headers, json={"decision": "REJECT", "rejection_reason": " "}).status_code == 422
    rejected = client.post(
        f"{path}/{application_id}/review", headers=admin_headers,
        json={"decision": "REJECT", "rejection_reason": "需要补充仓库资料"},
    )
    assert rejected.status_code == 200, rejected.text
    assert rejected.json()["status"] == "REJECTED" and rejected.json()["rejection_reason"] == "需要补充仓库资料"
    with factory() as session:
        assert session.scalar(select(WarehouseAccess).where(WarehouseAccess.shop_id == shop_id)) is None
        event = session.scalar(select(AuditEvent).where(
            AuditEvent.event_type == "warehouse_access_application_reviewed", AuditEvent.target_id == application_id
        ))
        assert event is not None
        assert event.actor_user_id == admin_id
        assert event.event_data == {"shop_id": shop_id, "warehouse_id": session.scalar(select(Warehouse.id).where(Warehouse.merchant_id == merchant_id)), "reviewer_user_id": admin_id, "decision": "REJECT"}

    new_application = client.post(path, headers=owner_headers)
    assert new_application.status_code == 201, new_application.text
    pending_id = new_application.json()["id"]
    approved = client.post(f"{path}/{pending_id}/review", headers=admin_headers, json={"decision": "APPROVE"})
    assert approved.status_code == 200, approved.text
    assert approved.json()["status"] == "APPROVED" and approved.json()["rejection_reason"] is None
    assert client.post(f"{path}/{pending_id}/review", headers=admin_headers, json={"decision": "APPROVE"}).status_code == 409
    assert client.post(path, headers=owner_headers).status_code == 409
    with factory() as session:
        grant = session.scalar(select(WarehouseAccess).where(WarehouseAccess.shop_id == shop_id))
        assert grant is not None and grant.merchant_id == merchant_id and grant.granted_by_user_id == admin_id
        assert is_authorized(session, shop_id) is True
        assert is_authorized(session, shop_id + 999999) is False
        event = session.scalar(select(AuditEvent).where(
            AuditEvent.event_type == "warehouse_access_application_reviewed", AuditEvent.target_id == pending_id
        ))
        assert event is not None and event.event_data["decision"] == "APPROVE"
    history = client.get(path, headers=admin_headers, params={"status": "REJECTED"}).json()
    assert history["total"] == 1 and history["items"][0]["id"] == application_id


def test_missing_warehouse_and_new_request_validation(auth_client):
    client, factory = auth_client
    admin_id, admin_headers = _account(client, "no-warehouse-admin")
    owner_id, owner_headers = _account(client, "no-warehouse-owner")
    _merchant_id, shop_id = _merchant_shop(factory, admin_id, owner_id, warehouse=False)
    path = "/api/v1/inventory/access-applications"
    response = client.post(path, headers=owner_headers)
    assert response.status_code == 409
    assert client.get(path, headers=owner_headers).json()["total"] == 0


def test_current_owner_and_admin_are_rechecked_after_lock_and_old_history_is_immutable(auth_client):
    client, factory = auth_client
    admin_id, admin_headers = _account(client, "changing-admin")
    owner_id, owner_headers = _account(client, "changing-owner")
    new_owner_id, new_owner_headers = _account(client, "new-owner")
    merchant_id, shop_id = _merchant_shop(factory, admin_id, owner_id)
    path = "/api/v1/inventory/access-applications"
    old = client.post(path, headers=owner_headers).json()
    with factory.begin() as session:
        session.get(Shop, shop_id).owner_user_id = new_owner_id
    # Former owner cannot file or approve a new grant. Rejection remains available to close old history.
    assert client.post(path, headers=owner_headers).status_code == 403
    denied = client.post(f"{path}/{old['id']}/review", headers=admin_headers, json={"decision": "APPROVE"})
    assert denied.status_code == 409
    assert client.get(path, headers=admin_headers).json()["items"][0]["status"] == "PENDING"
    rejected = client.post(
        f"{path}/{old['id']}/review", headers=admin_headers,
        json={"decision": "REJECT", "rejection_reason": "owner changed"},
    )
    assert rejected.status_code == 200 and rejected.json()["status"] == "REJECTED"
    newer = client.post(path, headers=new_owner_headers)
    assert newer.status_code == 201
    with factory.begin() as session:
        session.get(Merchant, merchant_id).admin_user_id = owner_id
    # The former owner now has no merchant-admin identity and cannot approve as such.
    stale_admin = client.post(
        f"{path}/{newer.json()['id']}/review", headers=admin_headers, json={"decision": "APPROVE"}
    )
    assert stale_admin.status_code == 403


def test_flush_failure_rolls_back_approval_and_access_as_one_business_transaction(auth_client, monkeypatch):
    client, factory = auth_client
    admin_id, _admin_headers = _account(client, "flush-admin")
    owner_id, _owner_headers = _account(client, "flush-owner")
    merchant_id, shop_id = _merchant_shop(factory, admin_id, owner_id)
    with factory.begin() as session:
        application = WarehouseAccessApplication(shop_id=shop_id, applicant_user_id=owner_id)
        session.add(application)
        session.flush()
        application_id = application.id
    with factory() as session:
        applicant_id, expected_shop_id = WarehouseAccessService.prepare_review(session, application_id)
        WarehouseAccessService.end_read_only_transaction(session)

    from app.services import warehouse_access as module
    original_flush = module.Session.flush
    flushed = {"called": False}

    def fail_after_real_flush(session, *args, **kwargs):
        grant_is_being_flushed = any(isinstance(item, WarehouseAccess) for item in session.new)
        original_flush(session, *args, **kwargs)
        if grant_is_being_flushed:
            flushed["called"] = True
            raise RuntimeError("simulated failure after both real rows were flushed")

    # The injected exception follows SQLAlchemy's real flush of both approval and grant.
    monkeypatch.setattr(module.Session, "flush", fail_after_real_flush)
    try:
        with factory() as session:
            with pytest.raises(RuntimeError, match="simulated failure"):
                WarehouseAccessService.review(
                    session, reviewer_user_id=admin_id, expected_merchant_id=merchant_id,
                    application_id=application_id, expected_applicant_user_id=applicant_id,
                    expected_shop_id=expected_shop_id, decision=ApplicationDecision.APPROVE, rejection_reason=None,
                    audit_writer=lambda **_kwargs: None,
                )
    finally:
        monkeypatch.setattr(module.Session, "flush", original_flush)
    assert flushed["called"]
    with factory() as session:
        assert session.get(WarehouseAccessApplication, application_id).status == "PENDING"
        assert session.scalar(select(WarehouseAccess).where(WarehouseAccess.shop_id == shop_id)) is None
        assert is_authorized(session, shop_id) is False


def test_database_constraints_reject_cross_merchant_grants_and_duplicate_shop_grants(auth_client):
    client, factory = auth_client
    admin_id, _admin_headers = _account(client, "fk-admin")
    owner_id, _owner_headers = _account(client, "fk-owner")
    other_admin_id, _other_headers = _account(client, "fk-other-admin")
    other_owner_id, _other_owner_headers = _account(client, "fk-other-owner")
    merchant_id, shop_id = _merchant_shop(factory, admin_id, owner_id, label="fk-one")
    other_merchant_id, _other_shop_id = _merchant_shop(factory, other_admin_id, other_owner_id, label="fk-two")
    with factory() as session:
        first_warehouse_id = session.scalar(select(Warehouse.id).where(Warehouse.merchant_id == merchant_id))
        other_warehouse_id = session.scalar(select(Warehouse.id).where(Warehouse.merchant_id == other_merchant_id))
    with pytest.raises(IntegrityError):
        with factory.begin() as session:
            session.add(WarehouseAccess(
                shop_id=shop_id, warehouse_id=other_warehouse_id, merchant_id=merchant_id, granted_by_user_id=admin_id
            ))
    with factory.begin() as session:
        session.add(WarehouseAccess(
            shop_id=shop_id, warehouse_id=first_warehouse_id, merchant_id=merchant_id, granted_by_user_id=admin_id
        ))
    with pytest.raises(IntegrityError):
        with factory.begin() as session:
            session.add(WarehouseAccess(
                shop_id=shop_id, warehouse_id=first_warehouse_id, merchant_id=merchant_id, granted_by_user_id=admin_id
            ))


def test_same_shop_concurrent_submission_and_review_commit_only_one_grant(auth_client):
    client, factory = auth_client
    admin_id, admin_headers = _account(client, "concurrent-admin")
    owner_id, owner_headers = _account(client, "concurrent-owner")
    _merchant_id, shop_id = _merchant_shop(factory, admin_id, owner_id)
    path = "/api/v1/inventory/access-applications"
    gate = Barrier(2)

    def submit():
        gate.wait()
        return client.post(path, headers=owner_headers)

    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = [pool.submit(submit) for _ in range(2)]
        submit_results = [future.result(timeout=15) for future in responses]
    assert sorted(response.status_code for response in submit_results) == [201, 409]
    application_id = next(response.json()["id"] for response in submit_results if response.status_code == 201)
    review_gate = Barrier(2)

    def approve():
        review_gate.wait()
        return client.post(f"{path}/{application_id}/review", headers=admin_headers, json={"decision": "APPROVE"})

    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = [pool.submit(approve) for _ in range(2)]
        review_results = [future.result(timeout=15) for future in responses]
    assert sorted(response.status_code for response in review_results) == [200, 409]
    with factory() as session:
        assert session.scalar(select(WarehouseAccess.id).where(WarehouseAccess.shop_id == shop_id)) is not None
        assert is_authorized(session, shop_id)


def test_old_rejected_application_cannot_compete_with_new_pending_approval(auth_client):
    client, factory = auth_client
    admin_id, admin_headers = _account(client, "history-race-admin")
    owner_id, owner_headers = _account(client, "history-race-owner")
    _merchant_id, shop_id = _merchant_shop(factory, admin_id, owner_id)
    path = "/api/v1/inventory/access-applications"
    old = client.post(path, headers=owner_headers).json()
    assert client.post(
        f"{path}/{old['id']}/review", headers=admin_headers,
        json={"decision": "REJECT", "rejection_reason": "try again"},
    ).status_code == 200
    current = client.post(path, headers=owner_headers).json()
    gate = Barrier(2)

    def review(application_id):
        gate.wait()
        return client.post(f"{path}/{application_id}/review", headers=admin_headers, json={"decision": "APPROVE"})

    with ThreadPoolExecutor(max_workers=2) as pool:
        old_result = pool.submit(review, old["id"])
        current_result = pool.submit(review, current["id"])
        results = [old_result.result(timeout=15), current_result.result(timeout=15)]
    assert sorted(response.status_code for response in results) == [200, 409]
    with factory() as session:
        assert session.get(WarehouseAccessApplication, old["id"]).status == "REJECTED"
        assert session.get(WarehouseAccessApplication, current["id"]).status == "APPROVED"
        assert session.scalar(select(WarehouseAccess.id).where(WarehouseAccess.shop_id == shop_id)) is not None


def test_submit_rechecks_owner_after_real_mysql_user_lock_wait(auth_client, monkeypatch):
    client, factory = auth_client
    admin_id, _admin_headers = _account(client, "owner-wait-admin")
    owner_id, owner_headers = _account(client, "owner-wait-old")
    new_owner_id, _new_headers = _account(client, "owner-wait-new")
    _merchant_id, shop_id = _merchant_shop(factory, admin_id, owner_id)
    holder = factory()
    holder.execute(select(User).where(User.id == owner_id).with_for_update())
    waiting = Event()
    from app.services import warehouse_access as service_module

    original_lock_users = service_module._lock_users

    def signal_then_lock(session, ids):
        if owner_id in ids:
            waiting.set()
        return original_lock_users(session, ids)

    monkeypatch.setattr(service_module, "_lock_users", signal_then_lock)
    path = "/api/v1/inventory/access-applications"
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(client.post, path, headers=owner_headers)
        assert waiting.wait(timeout=5)
        time.sleep(0.25)
        assert not future.done()
        # Change the actual shop relationship while the request is blocked on its owner row.
        holder.execute(select(User).where(User.id == new_owner_id).with_for_update())
        holder.execute(text("UPDATE shops SET owner_user_id = :new WHERE id = :shop"), {"new": new_owner_id, "shop": shop_id})
        holder.commit()
        holder.close()
        response = future.result(timeout=15)
    assert response.status_code == 403, response.text
    with factory() as session:
        assert session.scalar(select(WarehouseAccessApplication.id).where(WarehouseAccessApplication.shop_id == shop_id)) is None


def test_approve_rechecks_merchant_admin_after_real_mysql_user_lock_wait(auth_client, monkeypatch):
    client, factory = auth_client
    admin_id, admin_headers = _account(client, "admin-wait-old")
    new_admin_id, _new_headers = _account(client, "admin-wait-new")
    owner_id, owner_headers = _account(client, "admin-wait-owner")
    merchant_id, shop_id = _merchant_shop(factory, admin_id, owner_id)
    application = client.post("/api/v1/inventory/access-applications", headers=owner_headers).json()
    holder = factory()
    holder.execute(select(User).where(User.id == admin_id).with_for_update())
    waiting = Event()
    from app.services import warehouse_access as service_module

    original_lock_users = service_module._lock_users

    def signal_then_lock(session, ids):
        if admin_id in ids:
            waiting.set()
        return original_lock_users(session, ids)

    monkeypatch.setattr(service_module, "_lock_users", signal_then_lock)
    path = f"/api/v1/inventory/access-applications/{application['id']}/review"
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(client.post, path, headers=admin_headers, json={"decision": "APPROVE"})
        assert waiting.wait(timeout=5)
        time.sleep(0.25)
        assert not future.done()
        holder.execute(select(User).where(User.id == new_admin_id).with_for_update())
        holder.execute(text("UPDATE merchants SET admin_user_id = :new WHERE id = :merchant"), {"new": new_admin_id, "merchant": merchant_id})
        holder.commit()
        holder.close()
        response = future.result(timeout=15)
    assert response.status_code == 403, response.text
    with factory() as session:
        assert session.get(WarehouseAccessApplication, application["id"]).status == "PENDING"
        assert session.scalar(select(WarehouseAccess.id).where(WarehouseAccess.shop_id == shop_id)) is None


def test_independent_audit_failure_keeps_http_success_and_grant(auth_client, monkeypatch, caplog):
    client, factory = auth_client
    admin_id, admin_headers = _account(client, "audit-failure-admin")
    owner_id, owner_headers = _account(client, "audit-failure-owner")
    merchant_id, shop_id = _merchant_shop(factory, admin_id, owner_id)
    from app.services import audit as audit_module

    def fail_audit_session():
        raise RuntimeError("sensitive database details must not be logged")

    monkeypatch.setattr(audit_module, "get_session_factory", fail_audit_session)
    path = "/api/v1/inventory/access-applications"
    submitted = client.post(path, headers=owner_headers)
    assert submitted.status_code == 201, submitted.text
    approved = client.post(
        f"{path}/{submitted.json()['id']}/review", headers=admin_headers, json={"decision": "APPROVE"}
    )
    assert approved.status_code == 200, approved.text
    with factory() as session:
        assert session.scalar(select(WarehouseAccess.id).where(WarehouseAccess.shop_id == shop_id)) is not None
        assert session.scalar(select(AuditEvent.id).where(
            AuditEvent.target_type == "warehouse_access_application", AuditEvent.target_id == submitted.json()["id"]
        )) is None
    assert "audit write failed" in caplog.text
    assert "sensitive database details must not be logged" not in caplog.text


def test_approve_rechecks_applicant_owner_after_real_mysql_user_lock_wait(auth_client, monkeypatch):
    client, factory = auth_client
    admin_id, admin_headers = _account(client, "applicant-wait-admin")
    owner_id, owner_headers = _account(client, "applicant-wait-old")
    new_owner_id, _new_headers = _account(client, "applicant-wait-new")
    _merchant_id, shop_id = _merchant_shop(factory, admin_id, owner_id)
    application = client.post("/api/v1/inventory/access-applications", headers=owner_headers).json()
    holder = factory()
    holder.execute(select(User).where(User.id == owner_id).with_for_update())
    waiting = Event()
    from app.services import warehouse_access as service_module

    original_lock_users = service_module._lock_users

    def signal_then_lock(session, ids):
        if owner_id in ids:
            waiting.set()
        return original_lock_users(session, ids)

    monkeypatch.setattr(service_module, "_lock_users", signal_then_lock)
    path = f"/api/v1/inventory/access-applications/{application['id']}/review"
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(client.post, path, headers=admin_headers, json={"decision": "APPROVE"})
        assert waiting.wait(timeout=5)
        time.sleep(0.25)
        assert not future.done()
        holder.execute(select(User).where(User.id == new_owner_id).with_for_update())
        holder.execute(text("UPDATE shops SET owner_user_id = :new WHERE id = :shop"), {"new": new_owner_id, "shop": shop_id})
        holder.commit()
        holder.close()
        response = future.result(timeout=15)
    assert response.status_code == 409, response.text
    with factory() as session:
        assert session.get(WarehouseAccessApplication, application["id"]).status == "PENDING"
        assert session.scalar(select(WarehouseAccess.id).where(WarehouseAccess.shop_id == shop_id)) is None
