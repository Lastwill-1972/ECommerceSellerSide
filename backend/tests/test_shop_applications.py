from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
import secrets

import pytest
from sqlalchemy import func, select

from app.modules.merchants.models import AuditEvent, Merchant, MerchantApplication
from app.modules.merchants.schemas import ApplicationDecision
from app.modules.inventory.models import Warehouse
from app.modules.shops.models import Shop, ShopApplication
from app.modules.employees.models import ShopEmployeeRole
from app.modules.shops.schemas import ShopApplicationDecision
from app.modules.users.models import User
from app.services.identity import Role
from app.services.merchant_applications import (
    ApplicationConflict,
    ApplicationIneligible,
    MerchantApplicationService,
)
from app.services.shop_applications import (
    ShopApplicationConflict,
    ShopApplicationForbidden,
    ShopApplicationIneligible,
    ShopApplicationService,
)


PASSWORD = "correct-horse-battery"


def _register(client, label):
    suffix = secrets.token_hex(6)
    username = f"{label}-{suffix}"
    response = client.post(
        "/api/v1/auth/register",
        json={"username": username, "email": f"{username}@example.com", "password": PASSWORD},
    )
    assert response.status_code == 201, response.text
    user_id = response.json()["id"]
    token = client.post(
        "/api/v1/auth/login", json={"identifier": username, "password": PASSWORD}
    ).json()["access_token"]
    return user_id, {"Authorization": f"Bearer {token}"}


def _merchant(client, factory, label):
    user_id, headers = _register(client, label)
    with factory.begin() as session:
        merchant = Merchant(name=f"Merchant {label}", admin_user_id=user_id)
        session.add(merchant)
        session.flush()
        merchant_id = merchant.id
    return user_id, headers, merchant_id


def test_directory_submission_validation_pending_scope_and_safe_fields(auth_client):
    client, factory = auth_client
    admin_id, _admin_headers, merchant_id = _merchant(client, factory, "shop-dir-admin")
    _other_admin_id, other_admin_headers, other_merchant_id = _merchant(client, factory, "shop-dir-other")
    applicant_id, applicant_headers = _register(client, "shop-dir-applicant")
    with factory.begin() as session:
        session.get(User, admin_id).is_active = False

    directory = client.get("/api/v1/merchants?limit=100", headers=applicant_headers)
    assert directory.status_code == 200
    assert set(directory.json()) == {"items", "limit", "offset", "total"}
    assert all(set(item) == {"id", "name"} for item in directory.json()["items"])
    assert {item["id"] for item in directory.json()["items"]} == {merchant_id, other_merchant_id}

    created = client.post(
        "/api/v1/shops/applications",
        headers=applicant_headers,
        json={"merchant_id": merchant_id, "shop_name": "  First Shop  "},
    )
    assert created.status_code == 201, created.text
    assert created.json()["shop_name"] == "First Shop"
    assert created.json()["applicant_user_id"] == applicant_id
    assert created.json()["status"] == "PENDING"
    assert "owner_user_id" not in created.json()
    assert client.post(
        "/api/v1/shops/applications",
        headers=applicant_headers,
        json={"merchant_id": other_merchant_id, "shop_name": "Second Shop"},
    ).status_code == 409
    assert client.post(
        "/api/v1/shops/applications",
        headers=applicant_headers,
        json={"merchant_id": 9223372036854775000, "shop_name": "Unknown"},
    ).status_code == 404
    assert client.post(
        "/api/v1/shops/applications",
        headers=applicant_headers,
        json={"merchant_id": merchant_id, "shop_name": "Owner forge", "owner_user_id": admin_id},
    ).status_code == 422

    own = client.get(
        f"/api/v1/shops/applications?merchant_id={other_merchant_id}", headers=applicant_headers
    )
    assert own.status_code == 200
    assert {item["id"] for item in own.json()["items"]} == {created.json()["id"]}
    merchant_list = client.get(
        f"/api/v1/shops/applications?merchant_id={merchant_id}", headers=other_admin_headers
    )
    assert merchant_list.status_code == 200
    assert merchant_list.json()["total"] == 0
    assert client.get("/api/v1/shops", headers=other_admin_headers).status_code == 200


def test_existing_shop_name_owner_conflict_and_scoped_shop_reads(auth_client):
    client, factory = auth_client
    admin_id, admin_headers, merchant_id = _merchant(client, factory, "shop-existing-admin")
    second_admin_id, second_admin_headers, second_merchant_id = _merchant(client, factory, "shop-second-admin")
    applicant_id, applicant_headers = _register(client, "shop-existing-applicant")
    owner_id, owner_headers = _register(client, "shop-existing-owner")
    other_owner_id, other_owner_headers = _register(client, "shop-existing-other-owner")
    platform_id, platform_headers = _register(client, "shop-existing-platform")
    with factory.begin() as session:
        session.get(User, platform_id).is_platform_admin = True
        session.add_all([
            Shop(merchant_id=merchant_id, name="Existing", owner_user_id=owner_id),
            Shop(merchant_id=second_merchant_id, name="Existing", owner_user_id=other_owner_id),
        ])

    assert client.post(
        "/api/v1/shops/applications", headers=applicant_headers,
        json={"merchant_id": merchant_id, "shop_name": "existing"},
    ).status_code == 409
    owner_cannot_apply = client.post(
        "/api/v1/shops/applications", headers=owner_headers,
        json={"merchant_id": merchant_id, "shop_name": "Owner Another"},
    )
    assert owner_cannot_apply.status_code == 409

    first_admin_shops = client.get(f"/api/v1/shops?merchant_id={second_merchant_id}", headers=admin_headers)
    second_admin_shops = client.get("/api/v1/shops", headers=second_admin_headers)
    owner_shops = client.get("/api/v1/shops?merchant_id=0", headers=owner_headers)
    with factory() as session:
        expected_shop_ids = set(session.scalars(select(Shop.id).where(Shop.merchant_id == merchant_id)).all())
    assert {item["id"] for item in first_admin_shops.json()["items"]} == expected_shop_ids
    assert {item["merchant_id"] for item in second_admin_shops.json()["items"]} == {second_merchant_id}
    assert {item["owner_user_id"] for item in owner_shops.json()["items"]} == {owner_id}
    assert client.get("/api/v1/shops", headers=applicant_headers).status_code == 403
    assert client.get("/api/v1/shops", headers=platform_headers).status_code == 403


def test_review_approval_rejection_cross_merchant_and_audit(auth_client):
    client, factory = auth_client
    admin_id, admin_headers, merchant_id = _merchant(client, factory, "shop-review-admin")
    _other_admin_id, other_admin_headers, other_merchant_id = _merchant(client, factory, "shop-review-other")
    applicant_id, applicant_headers = _register(client, "shop-review-applicant")
    other_applicant_id, other_applicant_headers = _register(client, "shop-review-other-applicant")

    first = client.post(
        "/api/v1/shops/applications", headers=applicant_headers,
        json={"merchant_id": merchant_id, "shop_name": "To Approve"},
    )
    second = client.post(
        "/api/v1/shops/applications", headers=other_applicant_headers,
        json={"merchant_id": other_merchant_id, "shop_name": "To Reject"},
    )
    assert first.status_code == second.status_code == 201
    first_id, second_id = first.json()["id"], second.json()["id"]
    assert client.post(
        f"/api/v1/shops/applications/{first_id}/review", headers=applicant_headers,
        json={"decision": "APPROVE"},
    ).status_code == 403
    assert client.post(
        f"/api/v1/shops/applications/{first_id}/review", headers=other_admin_headers,
        json={"decision": "APPROVE"},
    ).status_code == 403
    approved = client.post(
        f"/api/v1/shops/applications/{first_id}/review", headers=admin_headers,
        json={"decision": "APPROVE"},
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()["status"] == "APPROVED"
    assert approved.json()["created_shop_id"] is not None
    with factory() as session:
        assert session.scalar(select(func.count(ShopEmployeeRole.id)).where(
            ShopEmployeeRole.shop_id == approved.json()["created_shop_id"],
            ShopEmployeeRole.name == "普通员工",
        )) == 1
    assert client.post(
        f"/api/v1/shops/applications/{first_id}/review", headers=admin_headers,
        json={"decision": "REJECT", "rejection_reason": "too late"},
    ).status_code == 409
    assert client.post(
        f"/api/v1/shops/applications/{second_id}/review", headers=other_admin_headers,
        json={"decision": "REJECT", "rejection_reason": "incomplete"},
    ).status_code == 200
    assert client.post(
        f"/api/v1/shops/applications/{second_id}/review", headers=other_admin_headers,
        json={"decision": "REJECT", "rejection_reason": "again"},
    ).status_code == 409
    assert client.get("/api/v1/shops", headers=applicant_headers).json()["total"] == 1
    with factory() as session:
        assert session.scalar(select(func.count(Shop.id))) == 1
        assert session.scalar(select(func.count(ShopApplication.id)).where(ShopApplication.status == "APPROVED")) == 1
        assert session.scalar(select(func.count(ShopApplication.id)).where(ShopApplication.status == "REJECTED")) == 1
        assert session.scalar(select(func.count(AuditEvent.id)).where(
            AuditEvent.target_type == "shop_application", AuditEvent.target_id == first_id,
        )) == 2
        # Shop and merchant application IDs overlap often; selecting by target pair is required.
        assert session.scalar(select(func.count(AuditEvent.id)).where(
            AuditEvent.target_type == "shop_application", AuditEvent.target_id == second_id,
        )) == 2
        assert session.scalar(select(func.count(Merchant.id))) == 2
        assert session.scalar(select(func.count(Shop.id))) == 1
        assert session.scalar(select(func.count(Warehouse.id))) == 0


def test_reject_stale_application_even_when_applicant_is_inactive_or_has_identity(auth_client):
    client, factory = auth_client
    admin_id, admin_headers, merchant_id = _merchant(client, factory, "shop-stale-admin")
    inactive_id, inactive_headers = _register(client, "shop-stale-inactive")
    identity_id, identity_headers = _register(client, "shop-stale-identity")
    with factory.begin() as session:
        session.get(User, inactive_id).is_active = False
        session.add(Merchant(name="Existing identity", admin_user_id=identity_id))
        inactive_app = ShopApplication(
            merchant_id=merchant_id, applicant_user_id=inactive_id, shop_name="Inactive", status="PENDING"
        )
        identity_app = ShopApplication(
            merchant_id=merchant_id, applicant_user_id=identity_id, shop_name="Has Identity", status="PENDING"
        )
        session.add_all([inactive_app, identity_app])
        session.flush()
        inactive_app_id, identity_app_id = inactive_app.id, identity_app.id

    assert client.post(
        f"/api/v1/shops/applications/{inactive_app_id}/review", headers=admin_headers,
        json={"decision": "REJECT", "rejection_reason": "inactive"},
    ).status_code == 200
    assert client.post(
        f"/api/v1/shops/applications/{identity_app_id}/review", headers=admin_headers,
        json={"decision": "REJECT", "rejection_reason": "already assigned"},
    ).status_code == 200
    with factory() as session:
        assert session.scalar(select(func.count(Shop.id))) == 0


def test_shop_audit_failure_keeps_committed_application_and_safe_log(auth_client, monkeypatch, caplog):
    client, factory = auth_client
    _admin_id, _admin_headers, merchant_id = _merchant(client, factory, "shop-audit-admin")
    _applicant_id, applicant_headers = _register(client, "shop-audit-applicant")

    def fail_factory():
        raise RuntimeError("sensitive simulated audit backend failure")

    monkeypatch.setattr("app.services.audit.get_session_factory", fail_factory)
    submitted = client.post(
        "/api/v1/shops/applications", headers=applicant_headers,
        json={"merchant_id": merchant_id, "shop_name": "Audit still returns"},
    )
    assert submitted.status_code == 201, submitted.text
    assert submitted.json()["status"] == "PENDING"
    assert "sensitive simulated audit backend failure" not in caplog.text
    assert "RuntimeError" in caplog.text
    with factory() as session:
        assert session.get(ShopApplication, submitted.json()["id"]).status == "PENDING"
        assert session.scalar(select(func.count(AuditEvent.id)).where(
            AuditEvent.target_type == "shop_application", AuditEvent.target_id == submitted.json()["id"]
        )) == 0


def test_shop_review_audit_failure_keeps_created_shop_and_approved_status(auth_client, monkeypatch, caplog):
    client, factory = auth_client
    _admin_id, admin_headers, merchant_id = _merchant(client, factory, "shop-approve-audit-admin")
    applicant_id, applicant_headers = _register(client, "shop-approve-audit-applicant")
    application = client.post(
        "/api/v1/shops/applications", headers=applicant_headers,
        json={"merchant_id": merchant_id, "shop_name": "Audit approval"},
    )
    assert application.status_code == 201

    def fail_factory():
        raise RuntimeError("private audit connection detail")

    monkeypatch.setattr("app.services.audit.get_session_factory", fail_factory)
    approved = client.post(
        f"/api/v1/shops/applications/{application.json()['id']}/review", headers=admin_headers,
        json={"decision": "APPROVE"},
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()["status"] == "APPROVED"
    assert "private audit connection detail" not in caplog.text
    with factory() as session:
        stored = session.get(ShopApplication, application.json()["id"])
        assert stored.status == "APPROVED" and stored.created_shop_id is not None
        assert session.scalar(select(func.count(Shop.id)).where(Shop.owner_user_id == applicant_id)) == 1
        assert session.scalar(select(func.count(AuditEvent.id)).where(
            AuditEvent.target_type == "shop_application", AuditEvent.target_id == stored.id,
        )) == 1


def test_application_recheck_detects_shop_created_after_submission_and_review_rolls_back(auth_client):
    client, factory = auth_client
    admin_id, admin_headers, merchant_id = _merchant(client, factory, "shop-recheck-admin")
    applicant_id, applicant_headers = _register(client, "shop-recheck-applicant")
    other_owner_id, _other_owner_headers = _register(client, "shop-recheck-owner")
    submitted = client.post(
        "/api/v1/shops/applications", headers=applicant_headers,
        json={"merchant_id": merchant_id, "shop_name": "Created later"},
    )
    assert submitted.status_code == 201
    with factory.begin() as session:
        session.add(Shop(merchant_id=merchant_id, name="Created later", owner_user_id=other_owner_id))
    review = client.post(
        f"/api/v1/shops/applications/{submitted.json()['id']}/review", headers=admin_headers,
        json={"decision": "APPROVE"},
    )
    assert review.status_code == 409
    with factory() as session:
        stored = session.get(ShopApplication, submitted.json()["id"])
        assert stored.status == "PENDING" and stored.created_shop_id is None
        assert session.scalar(select(func.count(Shop.id)).where(Shop.merchant_id == merchant_id)) == 1
        assert session.scalar(select(func.count(AuditEvent.id)).where(
            AuditEvent.event_type == "shop_application_reviewed",
            AuditEvent.target_type == "shop_application",
            AuditEvent.target_id == submitted.json()["id"],
        )) == 0


def test_flush_failure_after_default_employee_role_write_rolls_back_shop_and_application(auth_client, monkeypatch):
    client, factory = auth_client
    admin_id, admin_headers, merchant_id = _merchant(client, factory, "shop-flush-fail-admin")
    applicant_id, applicant_headers = _register(client, "shop-flush-fail-applicant")
    submitted = client.post(
        "/api/v1/shops/applications", headers=applicant_headers,
        json={"merchant_id": merchant_id, "shop_name": "Rollback shop"},
    )
    assert submitted.status_code == 201
    application_id = submitted.json()["id"]
    original_factory = factory

    def review_with_injected_failure():
        with original_factory() as session:
            original_flush = session.flush
            flush_count = 0

            def fail_after_shop_flush(*args, **kwargs):
                nonlocal flush_count
                result = original_flush(*args, **kwargs)
                flush_count += 1
                if flush_count == 2:
                    raise RuntimeError("injected failure after default employee role write")
                return result

            monkeypatch.setattr(session, "flush", fail_after_shop_flush)
            return ShopApplicationService.review(
                session,
                reviewer_user_id=admin_id,
                application_id=application_id,
                expected_applicant_user_id=applicant_id,
                expected_merchant_id=merchant_id,
                expected_admin_user_id=admin_id,
                decision=ShopApplicationDecision.APPROVE,
                rejection_reason=None,
            )

    with pytest.raises(RuntimeError, match="after default employee role write"):
        review_with_injected_failure()
    with factory() as session:
        stored = session.get(ShopApplication, application_id)
        assert stored.status == "PENDING" and stored.reviewer_user_id is None
        assert stored.created_shop_id is None and stored.reviewed_at is None
        assert session.scalar(select(func.count(Shop.id)).where(Shop.merchant_id == merchant_id)) == 0
        assert session.scalar(
            select(func.count(ShopEmployeeRole.id))
            .join(Shop, Shop.id == ShopEmployeeRole.shop_id)
            .where(Shop.merchant_id == merchant_id)
        ) == 0
        assert session.scalar(select(func.count(AuditEvent.id)).where(
            AuditEvent.event_type == "shop_application_reviewed",
            AuditEvent.target_type == "shop_application",
            AuditEvent.target_id == application_id,
        )) == 0


def test_different_merchants_may_approve_same_shop_name(auth_client):
    client, _factory = auth_client
    _admin_a_id, admin_a_headers, merchant_a_id = _merchant(client, _factory, "same-name-merchant-a")
    _admin_b_id, admin_b_headers, merchant_b_id = _merchant(client, _factory, "same-name-merchant-b")
    _applicant_a_id, applicant_a_headers = _register(client, "same-name-applicant-a")
    _applicant_b_id, applicant_b_headers = _register(client, "same-name-applicant-b")
    applications = []
    for merchant_id, applicant_headers in (
        (merchant_a_id, applicant_a_headers), (merchant_b_id, applicant_b_headers)
    ):
        response = client.post(
            "/api/v1/shops/applications", headers=applicant_headers,
            json={"merchant_id": merchant_id, "shop_name": "Shared display name"},
        )
        assert response.status_code == 201, response.text
        applications.append(response.json()["id"])
    for application_id, admin_headers in zip(applications, (admin_a_headers, admin_b_headers)):
        approved = client.post(
            f"/api/v1/shops/applications/{application_id}/review", headers=admin_headers,
            json={"decision": "APPROVE"},
        )
        assert approved.status_code == 200, approved.text
        assert approved.json()["status"] == "APPROVED"


def test_two_shop_approvals_cannot_assign_one_user_two_shop_owner_roles(mysql_test_session_factory):
    factory = mysql_test_session_factory
    with factory.begin() as session:
        applicant = User(username=f"two-shop-applicant-{secrets.token_hex(5)}", email=f"{secrets.token_hex(5)}@test", password_hash="x")
        admins = [
            User(username=f"two-shop-admin-{i}-{secrets.token_hex(4)}", email=f"{secrets.token_hex(5)}@test", password_hash="x")
            for i in range(2)
        ]
        session.add_all([applicant, *admins])
        session.flush()
        merchants = [Merchant(name=f"Two shop merchant {i}", admin_user_id=admin.id) for i, admin in enumerate(admins)]
        session.add_all(merchants)
        session.flush()
        applicant_id = applicant.id
        targets = [(merchant.id, admin.id) for merchant, admin in zip(merchants, admins)]
        applications = [
            ShopApplication(merchant_id=merchant.id, applicant_user_id=applicant_id, shop_name=f"Two shop {i}")
            for i, merchant in enumerate(merchants)
        ]
        session.add_all(applications)
        session.flush()
        application_ids = [application.id for application in applications]
    barrier = Barrier(3)

    def approve(index):
        merchant_id, admin_id = targets[index]
        barrier.wait(timeout=10)
        with factory() as session:
            try:
                ShopApplicationService.review(
                    session,
                    reviewer_user_id=admin_id,
                    application_id=application_ids[index],
                    expected_applicant_user_id=applicant_id,
                    expected_merchant_id=merchant_id,
                    expected_admin_user_id=admin_id,
                    decision=ShopApplicationDecision.APPROVE,
                    rejection_reason=None,
                )
                return "approved"
            except ShopApplicationIneligible:
                return "ineligible"

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(approve, index) for index in range(2)]
        barrier.wait(timeout=10)
        results = [future.result(timeout=20) for future in futures]
    assert results.count("approved") == 1
    assert results.count("ineligible") == 1
    with factory() as session:
        assert session.scalar(select(func.count(Shop.id)).where(Shop.owner_user_id == applicant_id)) == 1


def test_concurrent_review_of_one_shop_application_transitions_once(mysql_test_session_factory):
    factory = mysql_test_session_factory
    with factory.begin() as session:
        admin = User(username=f"same-app-review-admin-{secrets.token_hex(4)}", email=f"{secrets.token_hex(5)}@test", password_hash="x")
        applicant = User(username=f"same-app-review-app-{secrets.token_hex(4)}", email=f"{secrets.token_hex(5)}@test", password_hash="x")
        session.add_all([admin, applicant])
        session.flush()
        merchant = Merchant(name="Same application review merchant", admin_user_id=admin.id)
        session.add(merchant)
        session.flush()
        application = ShopApplication(merchant_id=merchant.id, applicant_user_id=applicant.id, shop_name="Only once")
        session.add(application)
        session.flush()
        admin_id, applicant_id, merchant_id, application_id = admin.id, applicant.id, merchant.id, application.id
    barrier = Barrier(3)

    def decide(decision):
        barrier.wait(timeout=10)
        with factory() as session:
            try:
                ShopApplicationService.review(
                    session,
                    reviewer_user_id=admin_id,
                    application_id=application_id,
                    expected_applicant_user_id=applicant_id,
                    expected_merchant_id=merchant_id,
                    expected_admin_user_id=admin_id,
                    decision=decision,
                    rejection_reason="review decision" if decision is ShopApplicationDecision.REJECT else None,
                )
                return "reviewed"
            except ShopApplicationConflict:
                return "conflict"

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(decide, decision) for decision in (
            ShopApplicationDecision.APPROVE, ShopApplicationDecision.REJECT
        )]
        barrier.wait(timeout=10)
        results = [future.result(timeout=20) for future in futures]
    assert results.count("reviewed") == 1
    assert results.count("conflict") == 1
    with factory() as session:
        assert session.scalar(select(func.count(Shop.id)).where(Shop.owner_user_id == applicant_id)) in {0, 1}
        stored = session.get(ShopApplication, application_id)
        assert stored.status in {"APPROVED", "REJECTED"}


def test_review_races_for_same_shop_name_are_serialized_and_unique(mysql_test_session_factory):
    factory = mysql_test_session_factory
    with factory.begin() as session:
        admin = User(username=f"same-name-admin-{secrets.token_hex(4)}", email=f"{secrets.token_hex(5)}@test", password_hash="x")
        applicants = [
            User(username=f"same-name-app-{i}-{secrets.token_hex(4)}", email=f"{secrets.token_hex(5)}@test", password_hash="x")
            for i in range(2)
        ]
        session.add_all([admin, *applicants])
        session.flush()
        merchant = Merchant(name="Same name merchant", admin_user_id=admin.id)
        session.add(merchant)
        session.flush()
        admin_id, merchant_id = admin.id, merchant.id
        applicant_ids = [user.id for user in applicants]
        applications = [
            ShopApplication(merchant_id=merchant_id, applicant_user_id=user_id, shop_name="Racing name", status="PENDING")
            for user_id in applicant_ids
        ]
        session.add_all(applications)
        session.flush()
        application_ids = [application.id for application in applications]
    barrier = Barrier(3)

    def approve(index):
        barrier.wait(timeout=10)
        with factory() as session:
            try:
                ShopApplicationService.review(
                    session,
                    reviewer_user_id=admin_id,
                    application_id=application_ids[index],
                    expected_applicant_user_id=applicant_ids[index],
                    expected_merchant_id=merchant_id,
                    expected_admin_user_id=admin_id,
                    decision=ShopApplicationDecision.APPROVE,
                    rejection_reason=None,
                )
                return "approved"
            except ShopApplicationConflict:
                return "conflict"

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(approve, index) for index in range(2)]
        barrier.wait(timeout=10)
        results = [future.result(timeout=20) for future in futures]
    assert results.count("approved") == 1
    assert results.count("conflict") == 1
    with factory() as session:
        assert session.scalar(select(func.count(Shop.id)).where(Shop.merchant_id == merchant_id)) == 1
        assert session.scalar(select(func.count(ShopApplication.id)).where(
            ShopApplication.merchant_id == merchant_id, ShopApplication.status == "APPROVED"
        )) == 1


def test_concurrent_shop_applications_for_one_user_allow_only_one_pending(mysql_test_session_factory):
    factory = mysql_test_session_factory
    with factory.begin() as session:
        applicant = User(username=f"one-pending-{secrets.token_hex(5)}", email=f"{secrets.token_hex(5)}@test", password_hash="x")
        admins = [
            User(username=f"one-pending-admin-{i}-{secrets.token_hex(4)}", email=f"{secrets.token_hex(5)}@test", password_hash="x")
            for i in range(2)
        ]
        session.add_all([applicant, *admins])
        session.flush()
        merchants = [Merchant(name=f"One pending merchant {i}", admin_user_id=admin.id) for i, admin in enumerate(admins)]
        session.add_all(merchants)
        session.flush()
        applicant_id = applicant.id
        targets = [(merchant.id, admin.id) for merchant, admin in zip(merchants, admins)]
    barrier = Barrier(3)

    def submit(index):
        merchant_id, admin_id = targets[index]
        barrier.wait(timeout=10)
        with factory() as session:
            try:
                ShopApplicationService.submit(
                    session,
                    applicant_user_id=applicant_id,
                    merchant_id=merchant_id,
                    expected_admin_user_id=admin_id,
                    shop_name=f"User application {index}",
                )
                return "submitted"
            except ShopApplicationConflict:
                return "conflict"

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(submit, index) for index in range(2)]
        barrier.wait(timeout=10)
        results = [future.result(timeout=20) for future in futures]
    assert results.count("submitted") == 1
    assert results.count("conflict") == 1
    with factory() as session:
        assert session.scalar(select(func.count(ShopApplication.id)).where(
            ShopApplication.applicant_user_id == applicant_id, ShopApplication.status == "PENDING"
        )) == 1


def test_mysql_duplicate_shop_insert_maps_to_conflict_and_rolls_back(mysql_test_session_factory, monkeypatch):
    factory = mysql_test_session_factory
    with factory.begin() as session:
        admin = User(username=f"duplicate-insert-admin-{secrets.token_hex(4)}", email=f"{secrets.token_hex(5)}@test", password_hash="x")
        owner = User(username=f"duplicate-insert-owner-{secrets.token_hex(4)}", email=f"{secrets.token_hex(5)}@test", password_hash="x")
        applicant = User(username=f"duplicate-insert-app-{secrets.token_hex(4)}", email=f"{secrets.token_hex(5)}@test", password_hash="x")
        session.add_all([admin, owner, applicant])
        session.flush()
        merchant = Merchant(name="Duplicate insert merchant", admin_user_id=admin.id)
        session.add(merchant)
        session.flush()
        existing = Shop(merchant_id=merchant.id, name="Racing insert", owner_user_id=owner.id)
        application = ShopApplication(merchant_id=merchant.id, applicant_user_id=applicant.id, shop_name="Racing insert")
        session.add_all([existing, application])
        session.flush()
        admin_id, owner_id, applicant_id = admin.id, owner.id, applicant.id
        merchant_id, app_id = merchant.id, application.id

    with factory() as session:
        original_scalar = session.scalar

        def hide_precheck(statement, *args, **kwargs):
            description = getattr(statement, "column_descriptions", ())
            if description and description[0].get("entity") is Shop:
                return None
            return original_scalar(statement, *args, **kwargs)

        monkeypatch.setattr(session, "scalar", hide_precheck)
        with pytest.raises(ShopApplicationConflict, match="owner or merchant shop name"):
            ShopApplicationService.review(
                session,
                reviewer_user_id=admin_id,
                application_id=app_id,
                expected_applicant_user_id=applicant_id,
                expected_merchant_id=merchant_id,
                expected_admin_user_id=admin_id,
                decision=ShopApplicationDecision.APPROVE,
                rejection_reason=None,
            )
    with factory() as session:
        stored = session.get(ShopApplication, app_id)
        assert stored.status == "PENDING" and stored.created_shop_id is None
        assert session.scalar(select(func.count(Shop.id)).where(Shop.merchant_id == merchant_id)) == 1


def test_shop_and_merchant_approvals_cannot_assign_one_user_two_identities(mysql_test_session_factory):
    factory = mysql_test_session_factory
    with factory.begin() as session:
        applicant = User(username=f"dual-applicant-{secrets.token_hex(5)}", email=f"{secrets.token_hex(5)}@test", password_hash="x")
        platform = User(username=f"dual-platform-{secrets.token_hex(5)}", email=f"{secrets.token_hex(5)}@test", password_hash="x", is_platform_admin=True)
        merchant_admin = User(username=f"dual-admin-{secrets.token_hex(5)}", email=f"{secrets.token_hex(5)}@test", password_hash="x")
        session.add_all([applicant, platform, merchant_admin])
        session.flush()
        merchant = Merchant(name="Dual identity shop merchant", admin_user_id=merchant_admin.id)
        session.add(merchant)
        session.flush()
        merchant_app = MerchantApplication(applicant_user_id=applicant.id, merchant_name="Dual identity merchant", status="PENDING")
        shop_app = ShopApplication(merchant_id=merchant.id, applicant_user_id=applicant.id, shop_name="Dual identity shop", status="PENDING")
        session.add_all([merchant_app, shop_app])
        session.flush()
        applicant_id, platform_id, admin_id = applicant.id, platform.id, merchant_admin.id
        merchant_application_id, shop_application_id, merchant_id = merchant_app.id, shop_app.id, merchant.id
    barrier = Barrier(3)

    def approve_merchant():
        barrier.wait(timeout=10)
        with factory() as session:
            try:
                MerchantApplicationService.review(
                    session,
                    reviewer_user_id=platform_id,
                    application_id=merchant_application_id,
                    expected_applicant_user_id=applicant_id,
                    decision=ApplicationDecision.APPROVE,
                    rejection_reason=None,
                )
                return "merchant"
            except ApplicationConflict:
                return "conflict"
            except ApplicationIneligible:
                return "conflict"

    def approve_shop():
        barrier.wait(timeout=10)
        with factory() as session:
            try:
                ShopApplicationService.review(
                    session,
                    reviewer_user_id=admin_id,
                    application_id=shop_application_id,
                    expected_applicant_user_id=applicant_id,
                    expected_merchant_id=merchant_id,
                    expected_admin_user_id=admin_id,
                    decision=ShopApplicationDecision.APPROVE,
                    rejection_reason=None,
                )
                return "shop"
            except ShopApplicationIneligible:
                return "conflict"
            except ShopApplicationConflict:
                return "conflict"

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(approve_merchant), executor.submit(approve_shop)]
        barrier.wait(timeout=10)
        results = [future.result(timeout=20) for future in futures]
    assert results.count("conflict") == 1
    assert results.count("merchant") + results.count("shop") == 1
    with factory() as session:
        merchant_identity = session.scalar(select(Merchant.id).where(Merchant.admin_user_id == applicant_id))
        shop_identity = session.scalar(select(Shop.id).where(Shop.owner_user_id == applicant_id))
        assert bool(merchant_identity) != bool(shop_identity)
        merchant_role_count = session.scalar(select(func.count(Merchant.id)).where(Merchant.admin_user_id == applicant_id))
        shop_role_count = session.scalar(select(func.count(Shop.id)).where(Shop.owner_user_id == applicant_id))
        assert merchant_role_count + shop_role_count == 1
