from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
import secrets

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.main import create_app
from app.modules.inventory.models import Warehouse
from app.modules.merchants.models import AuditEvent, Merchant, MerchantApplication
from app.modules.merchants.schemas import ApplicationDecision
from app.modules.shops.models import Shop
from app.modules.users.models import User
from app.services import merchant_applications as application_service_module
from app.services.audit import AuditService
from app.services.identity import IdentityService, Role
from app.services.merchant_applications import (
    ApplicationConflict,
    ApplicationForbidden,
    ApplicationIneligible,
    MerchantApplicationService,
)
from scripts.init_platform_admin import initialize_platform_admin


PASSWORD = "correct-horse-battery"


def _register(client, label):
    suffix = secrets.token_hex(6)
    username = f"{label}-{suffix}"
    response = client.post(
        "/api/v1/auth/register",
        json={"username": username, "email": f"{username}@example.com", "password": PASSWORD},
    )
    assert response.status_code == 201
    token = client.post(
        "/api/v1/auth/login", json={"identifier": username, "password": PASSWORD}
    ).json()["access_token"]
    return response.json()["id"], username, {"Authorization": f"Bearer {token}"}


def _make_platform_admin(client, factory, label="platform"):
    _user_id, username, _headers = _register(client, label)
    assert "initialized" in initialize_platform_admin(username, username, session_factory=factory).lower()
    return username


def _login_headers(client, username):
    token = client.post(
        "/api/v1/auth/login", json={"identifier": username, "password": PASSWORD}
    ).json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def test_application_api_scopes_lists_and_approves_atomically(auth_client):
    client, factory = auth_client
    applicant_id, _username, applicant_headers = _register(client, "apply")
    other_id, _other_username, other_headers = _register(client, "apply-other")
    platform_username = _make_platform_admin(client, factory)
    platform_headers = _login_headers(client, platform_username)

    submitted = client.post(
        "/api/v1/merchants/applications",
        headers=applicant_headers,
        json={"merchant_name": "  Acme Store  "},
    )
    assert submitted.status_code == 201, submitted.text
    app_id = submitted.json()["id"]
    assert submitted.json()["merchant_name"] == "Acme Store"
    assert submitted.json()["status"] == "PENDING"

    other = client.post(
        "/api/v1/merchants/applications",
        headers=other_headers,
        json={"merchant_name": "Other Shop"},
    )
    assert other.status_code == 201

    assert client.post(
        "/api/v1/merchants/applications",
        headers=applicant_headers,
        json={"merchant_name": "Duplicate"},
    ).status_code == 409
    assert client.get("/api/v1/merchants/applications", headers=applicant_headers).json()["total"] == 1
    platform_list = client.get("/api/v1/merchants/applications", headers=platform_headers)
    assert platform_list.status_code == 200
    assert platform_list.json()["total"] == 2
    assert all(item["status"] == "PENDING" for item in platform_list.json()["items"])
    assert client.get(
        "/api/v1/merchants/applications?status=APPROVED", headers=platform_headers
    ).status_code == 422

    decision = client.post(
        f"/api/v1/merchants/applications/{app_id}/review",
        headers=platform_headers,
        json={"decision": "APPROVE"},
    )
    assert decision.status_code == 200, decision.text
    assert decision.json()["status"] == "APPROVED"
    merchant_id = decision.json()["created_merchant_id"]
    assert decision.json()["reviewer_user_id"] is not None
    assert decision.json()["reviewed_at"] is not None

    with factory() as session:
        merchant = session.scalar(select(Merchant).where(Merchant.id == merchant_id))
        assert merchant is not None and merchant.admin_user_id == applicant_id
        warehouses = session.scalars(select(Warehouse).where(Warehouse.merchant_id == merchant_id)).all()
        assert len(warehouses) == 1 and warehouses[0].name == "Default warehouse"
        events = session.scalars(
            select(AuditEvent).where(
                AuditEvent.target_type == "merchant_application",
                AuditEvent.target_id == app_id,
            )
        ).all()
        assert {(event.event_type, event.outcome) for event in events} == {
            ("merchant_application_submitted", "PENDING"),
            ("merchant_application_reviewed", "APPROVED"),
        }
        assert all(event.event_data == {} or event.event_data == {"decision": "APPROVE"} for event in events)
        assert IdentityService.get_identity(session, session.get(User, applicant_id)).role is Role.MERCHANT_ADMIN

    assert client.get("/api/v1/merchants/applications", headers=applicant_headers).json()["total"] == 1
    assert client.get("/api/v1/merchants/applications", headers=other_headers).json()["total"] == 1
    assert client.post(
        f"/api/v1/merchants/applications/{app_id}/review",
        headers=platform_headers,
        json={"decision": "REJECT", "rejection_reason": "duplicate"},
    ).status_code == 409
    assert client.post(
        "/api/v1/merchants/applications",
        headers=applicant_headers,
        json={"merchant_name": "Second merchant"},
    ).status_code == 409


def test_rejection_and_review_permissions_and_validation(auth_client):
    client, factory = auth_client
    _applicant_id, _username, applicant_headers = _register(client, "reject")
    _other_id, _other_username, other_headers = _register(client, "review-none")
    platform_username = _make_platform_admin(client, factory, "review-admin")
    platform_headers = _login_headers(client, platform_username)
    application = client.post(
        "/api/v1/merchants/applications",
        headers=applicant_headers,
        json={"merchant_name": "Reject Me"},
    ).json()
    app_id = application["id"]

    assert client.post(
        f"/api/v1/merchants/applications/{app_id}/review",
        headers=other_headers,
        json={"decision": "REJECT", "rejection_reason": "test"},
    ).status_code == 403
    assert client.post(
        f"/api/v1/merchants/applications/{app_id}/review",
        headers=platform_headers,
        json={"decision": "REJECT"},
    ).status_code == 422
    rejected = client.post(
        f"/api/v1/merchants/applications/{app_id}/review",
        headers=platform_headers,
        json={"decision": "REJECT", "rejection_reason": "  incomplete paperwork  "},
    )
    assert rejected.status_code == 200, rejected.text
    assert rejected.json()["status"] == "REJECTED"
    assert rejected.json()["rejection_reason"] == "incomplete paperwork"
    with factory() as session:
        assert session.scalar(select(func.count(Merchant.id))) == 0
        assert session.scalar(select(func.count(Warehouse.id))) == 0
        event = session.scalar(
            select(AuditEvent).where(AuditEvent.event_type == "merchant_application_reviewed")
        )
        assert event.outcome == "REJECTED"
        assert event.event_data == {"decision": "REJECT"}


def test_can_reject_another_pending_application_after_applicant_is_approved(auth_client):
    client, factory = auth_client
    applicant_id, _username, applicant_headers = _register(client, "approved-with-stale-pending")
    admin_username = _make_platform_admin(client, factory, "stale-pending-admin")
    admin_headers = _login_headers(client, admin_username)
    first_application = client.post(
        "/api/v1/merchants/applications",
        headers=applicant_headers,
        json={"merchant_name": "Approved merchant"},
    ).json()
    with factory.begin() as session:
        second_application = MerchantApplication(
            applicant_user_id=applicant_id,
            merchant_name="Stale pending request",
            status="PENDING",
        )
        session.add(second_application)
        session.flush()
        second_application_id = second_application.id

    approved = client.post(
        f"/api/v1/merchants/applications/{first_application['id']}/review",
        headers=admin_headers,
        json={"decision": "APPROVE"},
    )
    assert approved.status_code == 200
    rejected = client.post(
        f"/api/v1/merchants/applications/{second_application_id}/review",
        headers=admin_headers,
        json={"decision": "REJECT", "rejection_reason": "duplicate request"},
    )
    assert rejected.status_code == 200
    assert rejected.json()["status"] == "REJECTED"
    with factory() as session:
        assert session.scalar(select(func.count(Merchant.id))) == 1
        assert session.scalar(select(func.count(Warehouse.id))) == 1


def test_applicant_cannot_self_review(mysql_test_session_factory):
    factory = mysql_test_session_factory
    suffix = secrets.token_hex(6)
    username = f"self-review-{suffix}"
    with factory.begin() as session:
        applicant = User(username=username, email=f"{username}@example.test", password_hash="unused")
        session.add(applicant)
        session.flush()
        applicant_id = applicant.id
        application = MerchantApplication(
            applicant_user_id=applicant_id,
            merchant_name="Self Review",
            status="PENDING",
        )
        session.add(application)
        session.flush()
        application_id = application.id
    with factory() as session:
        with pytest.raises(ApplicationForbidden, match="own applications"):
            MerchantApplicationService.review(
                session,
                reviewer_user_id=applicant_id,
                application_id=application_id,
                expected_applicant_user_id=applicant_id,
                decision=ApplicationDecision.REJECT,
                rejection_reason="not allowed",
            )
    with factory() as session:
        stored = session.get(MerchantApplication, application_id)
        assert stored.status == "PENDING" and stored.reviewer_user_id is None


@pytest.mark.parametrize("failure_point", ["_create_merchant", "_create_default_warehouse"])
def test_approval_faults_roll_back_merchant_warehouse_and_application(
    auth_client, mysql_test_session_factory, monkeypatch, failure_point
):
    _client, factory = auth_client
    applicant_id, _username, _headers = _register(_client, "fault-applicant")
    admin_username = _make_platform_admin(_client, factory, "fault-admin")
    with factory.begin() as session:
        reviewer_id = session.scalar(select(User.id).where(User.username == admin_username))
        application = MerchantApplication(
            applicant_user_id=applicant_id,
            merchant_name="Faulted Merchant",
            status="PENDING",
        )
        session.add(application)
        session.flush()
        application_id = application.id

    def fail(*_args, **_kwargs):
        raise RuntimeError("injected write failure")

    monkeypatch.setattr(application_service_module, failure_point, fail)
    with pytest.raises(RuntimeError, match="injected write failure"):
        with mysql_test_session_factory() as session:
            MerchantApplicationService.review(
                session,
                reviewer_user_id=reviewer_id,
                application_id=application_id,
                expected_applicant_user_id=applicant_id,
                decision=ApplicationDecision.APPROVE,
                rejection_reason=None,
            )

    with factory() as session:
        stored = session.get(MerchantApplication, application_id)
        assert stored.status == "PENDING"
        assert stored.reviewer_user_id is None and stored.created_merchant_id is None
        assert session.scalar(select(func.count(Merchant.id))) == 0
        assert session.scalar(select(func.count(Warehouse.id))) == 0
        assert session.scalar(select(func.count(AuditEvent.id))) == 0


def test_concurrent_approvals_use_independent_sessions_and_latest_identity(mysql_test_session_factory):
    factory = mysql_test_session_factory
    suffix = secrets.token_hex(6)
    applicant_name = f"concurrent-applicant-{suffix}"
    admin_names = [f"concurrent-admin-{i}-{suffix}" for i in range(2)]
    second_applicant_name = f"concurrent-second-applicant-{suffix}"
    with factory.begin() as session:
        applicant = User(
            username=applicant_name,
            email=f"{applicant_name}@example.test",
            password_hash="unused-hash",
        )
        second_applicant = User(
            username=second_applicant_name,
            email=f"{second_applicant_name}@example.test",
            password_hash="unused-hash",
        )
        admins = [
            User(
                username=name,
                email=f"{name}@example.test",
                password_hash="unused-hash",
                is_platform_admin=True,
            )
            for name in admin_names
        ]
        session.add_all([applicant, second_applicant, *admins])
        session.flush()
        applicant_id, second_applicant_id = applicant.id, second_applicant.id
        admin_ids = [admin.id for admin in admins]
        applications = [
            MerchantApplication(applicant_user_id=applicant_id, merchant_name=f"Concurrent {i}", status="PENDING")
            for i in range(2)
        ]
        same_application = MerchantApplication(
            applicant_user_id=second_applicant_id,
            merchant_name="Same application",
            status="PENDING",
        )
        session.add_all([*applications, same_application])
        session.flush()
        application_ids = [application.id for application in applications]
        same_application_id = same_application.id

    barrier = Barrier(3)

    def approve(application_id, admin_id, expected_applicant_id):
        barrier.wait(timeout=10)
        with factory() as session:
            try:
                MerchantApplicationService.review(
                    session,
                    reviewer_user_id=admin_id,
                    application_id=application_id,
                    expected_applicant_user_id=expected_applicant_id,
                    decision=ApplicationDecision.APPROVE,
                    rejection_reason=None,
                )
                return "approved"
            except ApplicationIneligible:
                return "ineligible"
            except ApplicationConflict:
                return "conflict"

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(approve, application_ids[i], admin_ids[i], applicant_id)
            for i in range(2)
        ]
        barrier.wait(timeout=10)
        results = [future.result(timeout=20) for future in futures]
    assert results.count("approved") == 1
    assert results.count("ineligible") == 1
    with factory() as session:
        assert session.scalar(select(func.count(Merchant.id))) == 1
        assert session.scalar(select(func.count(Warehouse.id))) == 1
        assert session.scalar(
            select(func.count(MerchantApplication.id)).where(MerchantApplication.status == "APPROVED")
        ) == 1

    same_app_barrier = Barrier(3)

    def approve_same(admin_id):
        same_app_barrier.wait(timeout=10)
        with factory() as session:
            try:
                MerchantApplicationService.review(
                    session,
                    reviewer_user_id=admin_id,
                    application_id=same_application_id,
                    expected_applicant_user_id=second_applicant_id,
                    decision=ApplicationDecision.REJECT,
                    rejection_reason="concurrent review",
                )
                return "reviewed"
            except ApplicationConflict:
                return "conflict"

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(approve_same, admin_id) for admin_id in admin_ids]
        same_app_barrier.wait(timeout=10)
        same_results = [future.result(timeout=20) for future in futures]
    assert same_results.count("reviewed") == 1
    assert same_results.count("conflict") == 1


def test_audit_failure_does_not_change_successful_business_response(auth_client, monkeypatch, caplog):
    client, factory = auth_client
    _applicant_id, _username, headers = _register(client, "audit-failure")
    admin_username = _make_platform_admin(client, factory, "audit-admin")
    admin_headers = _login_headers(client, admin_username)
    submitted = client.post(
        "/api/v1/merchants/applications", headers=headers, json={"merchant_name": "Audited Later"}
    )
    assert submitted.status_code == 201

    def fail_factory():
        raise RuntimeError("sensitive simulated audit backend failure")

    monkeypatch.setattr("app.services.audit.get_session_factory", fail_factory)
    approved = client.post(
        f"/api/v1/merchants/applications/{submitted.json()['id']}/review",
        headers=admin_headers,
        json={"decision": "APPROVE"},
    )
    assert approved.status_code == 200
    assert approved.json()["status"] == "APPROVED"
    assert "sensitive simulated audit backend failure" not in caplog.text
    assert "RuntimeError" in caplog.text
    with factory() as session:
        assert session.scalar(select(func.count(AuditEvent.id))) == 1
        assert session.scalar(
            select(func.count(AuditEvent.id)).where(AuditEvent.event_type == "merchant_application_reviewed")
        ) == 0
        assert session.scalar(select(func.count(MerchantApplication.id))) == 1
        assert session.scalar(select(func.count(Merchant.id))) == 1
        assert session.scalar(select(func.count(Warehouse.id))) == 1


def test_cli_initialization_is_explicit_idempotent_and_rejects_ineligible_users(mysql_test_session_factory):
    factory = mysql_test_session_factory
    suffix = secrets.token_hex(6)
    active_name = f"cli-active-{suffix}"
    merchant_name = f"cli-merchant-{suffix}"
    shop_name = f"cli-shop-{suffix}"
    inactive_name = f"cli-inactive-{suffix}"
    with factory.begin() as session:
        active = User(username=active_name, email=f"{active_name}@example.test", password_hash="unused")
        merchant_admin = User(username=merchant_name, email=f"{merchant_name}@example.test", password_hash="unused")
        shop_owner = User(username=shop_name, email=f"{shop_name}@example.test", password_hash="unused")
        inactive = User(
            username=inactive_name,
            email=f"{inactive_name}@example.test",
            password_hash="unused",
            is_active=False,
        )
        session.add_all([active, merchant_admin, shop_owner, inactive])
        session.flush()
        session.add(Merchant(name="Existing merchant", admin_user=merchant_admin))
        session.flush()
        merchant = session.scalar(select(Merchant).where(Merchant.admin_user_id == merchant_admin.id))
        session.add(Shop(name="Existing shop", merchant=merchant, owner_user=shop_owner))

    assert initialize_platform_admin(active_name, active_name, session_factory=factory).startswith("Platform administrator initialized")
    assert initialize_platform_admin(active_name, active_name, session_factory=factory).startswith("Platform administrator already")
    with pytest.raises(ValueError, match="exactly match"):
        initialize_platform_admin(active_name, "someone-else", session_factory=factory)
    with pytest.raises(LookupError, match="exact username"):
        initialize_platform_admin("missing-user", "missing-user", session_factory=factory)
    with pytest.raises(ValueError, match="merchant or shop"):
        initialize_platform_admin(merchant_name, merchant_name, session_factory=factory)
    with pytest.raises(ValueError, match="merchant or shop"):
        initialize_platform_admin(shop_name, shop_name, session_factory=factory)
    with pytest.raises(ValueError, match="inactive"):
        initialize_platform_admin(inactive_name, inactive_name, session_factory=factory)


def test_audit_service_rejects_untrusted_context_shapes():
    assert AuditService.record_after_commit(
        actor_user_id=1,
        event_type="merchant_application_reviewed",
        target_type="merchant_application",
        target_id=2,
        outcome="APPROVED",
        event_data={"decision": {"password": "not stored"}},
        session_factory=lambda: pytest.fail("invalid audit payload must be rejected before session creation"),
    ) is False


def test_audit_event_commits_in_a_distinct_session(mysql_test_session_factory, monkeypatch):
    factory = mysql_test_session_factory
    suffix = secrets.token_hex(6)
    username = f"audit-independent-{suffix}"
    with factory.begin() as setup_session:
        user = User(username=username, email=f"{username}@example.test", password_hash="unused")
        setup_session.add(user)
        setup_session.flush()
        user_id = user.id

    audit_sessions = []

    def audit_factory():
        session = factory()
        audit_sessions.append(session)
        return session

    monkeypatch.setattr("app.services.audit.get_session_factory", lambda: audit_factory)
    with factory() as business_session:
        result = MerchantApplicationService.submit(
            business_session,
            applicant_user_id=user_id,
            merchant_name="Independent audit",
        )
        assert result.status == "PENDING"
        assert audit_sessions and audit_sessions[0] is not business_session
        assert not audit_sessions[0].in_transaction()
    with factory() as verification:
        event = verification.scalar(
            select(AuditEvent).where(AuditEvent.target_id == result.id)
        )
        assert event.event_type == "merchant_application_submitted"
        assert event.outcome == "PENDING" and event.event_data == {}
    assert AuditService.record_after_commit(
        actor_user_id=1,
        event_type="merchant_application_reviewed",
        target_type="merchant_application",
        target_id=2,
        outcome="APPROVED",
        event_data={"decision": "APPROVE", "token": "not stored"},
        session_factory=lambda: pytest.fail("unknown audit fields must be rejected before session creation"),
    ) is False

