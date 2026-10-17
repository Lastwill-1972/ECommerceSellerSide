import secrets
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from sqlalchemy import func, select

from app.core.permissions import AccessDenied, AuthorizationService, OrderAction
from app.modules.employees.models import EmployeeApplication, ShopEmployee, ShopEmployeeRole
from app.modules.employees.schemas import EmployeeApplicationDecision
from app.modules.merchants.models import AuditEvent, Merchant, MerchantApplication
from app.modules.shops.models import Shop, ShopApplication
from app.modules.users.models import User
from app.services.employees import (
    EmployeeConflict,
    EmployeeIneligible,
    EmployeeNotFound,
    EmployeeService,
)
from app.services.audit import AuditService
from app.services.identity import IdentityConflict, IdentityService, Role


def _account(client, label):
    suffix = secrets.token_hex(5)
    username = f"{label}-{suffix}"
    email = f"{suffix}@example.com"
    registered = client.post(
        "/api/v1/auth/register",
        json={"username": username, "email": email, "password": "long-correct-password"},
    )
    assert registered.status_code == 201, registered.text
    token = client.post(
        "/api/v1/auth/login", json={"identifier": username, "password": "long-correct-password"}
    ).json()["access_token"]
    return registered.json()["id"], {"Authorization": f"Bearer {token}"}


def _shop(factory, owner_user_id, name=None):
    with factory.begin() as session:
        merchant_admin = User(
            username=f"employee-merchant-admin-{secrets.token_hex(5)}",
            email=f"{secrets.token_hex(5)}@example.test",
            password_hash="unused",
        )
        session.add(merchant_admin)
        session.flush()
        merchant = Merchant(name=f"Employee merchant {secrets.token_hex(4)}", admin_user=merchant_admin)
        session.add(merchant)
        session.flush()
        shop = Shop(merchant_id=merchant.id, name=name or f"Shop {secrets.token_hex(4)}", owner_user_id=owner_user_id)
        session.add(shop)
        session.flush()
        default_role = ShopEmployeeRole(shop_id=shop.id, name="普通员工")
        session.add(default_role)
        session.flush()
        return shop.id, merchant.id, default_role.id


def test_employee_application_all_routes_identity_role_permissions_and_reapply(auth_client):
    client, factory = auth_client
    owner_id, owner_headers = _account(client, "employee-owner")
    shop_id, merchant_id, default_role_id = _shop(factory, owner_id)
    applicant_id, applicant_headers = _account(client, "employee-applicant")

    assert client.post("/api/v1/employees/applications", headers=applicant_headers, json={"shop_id": 999999}).status_code == 404
    submitted = client.post(
        "/api/v1/employees/applications", headers=applicant_headers, json={"shop_id": shop_id}
    )
    assert submitted.status_code == 201, submitted.text
    application_id = submitted.json()["id"]
    assert submitted.json()["status"] == "PENDING"
    assert client.post(
        "/api/v1/employees/applications", headers=applicant_headers, json={"shop_id": shop_id}
    ).status_code == 409
    assert client.post(
        "/api/v1/employees/applications", headers=applicant_headers,
        json={"shop_id": shop_id, "applicant_user_id": applicant_id},
    ).status_code == 422
    assert client.post(
        f"/api/v1/employees/applications/{application_id}/review", headers=applicant_headers,
        json={"decision": "REJECT", "rejection_reason": "no"},
    ).status_code == 403
    assert client.post(
        f"/api/v1/employees/applications/{application_id}/review", headers=owner_headers,
        json={"decision": "REJECT"},
    ).status_code == 422

    owner_list = client.get("/api/v1/employees/applications?status=PENDING&limit=7&offset=0", headers=owner_headers)
    applicant_list = client.get("/api/v1/employees/applications?limit=7&offset=0", headers=applicant_headers)
    assert owner_list.status_code == applicant_list.status_code == 200
    assert owner_list.json()["total"] == applicant_list.json()["total"] == 1
    approved = client.post(
        f"/api/v1/employees/applications/{application_id}/review", headers=owner_headers,
        json={"decision": "APPROVE"},
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()["status"] == "APPROVED"

    identity_before = client.get("/api/v1/auth/me", headers=applicant_headers).json()
    assert identity_before["role"] == "SHOP_EMPLOYEE"
    assert identity_before["merchant_id"] == merchant_id and identity_before["shop_id"] == shop_id
    with factory() as session:
        employee = session.scalar(select(ShopEmployee).where(ShopEmployee.user_id == applicant_id))
        assert employee is not None and employee.role_id == default_role_id
        employee_id = employee.id
        role_id = session.scalar(select(ShopEmployeeRole.id).where(
            ShopEmployeeRole.shop_id == shop_id, ShopEmployeeRole.name == "普通员工"
        ))
        identity = IdentityService.get_identity(session, session.get(User, applicant_id))
        assert identity.role is Role.SHOP_EMPLOYEE
        assert AuthorizationService.check_shop(session, identity, shop_id) == merchant_id
        AuthorizationService.check_order_action(session, identity, shop_id=shop_id, action=OrderAction.CREATE)
        with pytest.raises(AccessDenied):
            AuthorizationService.check_shop_management(session, identity, shop_id)
        assert session.scalar(select(func.count(AuditEvent.id)).where(
            AuditEvent.event_type == "employee_application_submitted",
            AuditEvent.target_type == "employee_application",
            AuditEvent.target_id == application_id,
        )) == 1
        assert session.scalar(select(func.count(AuditEvent.id)).where(
            AuditEvent.event_type == "employee_application_reviewed",
            AuditEvent.target_type == "employee_application",
            AuditEvent.target_id == application_id,
        )) == 1

    employees_url = f"/api/v1/shops/{shop_id}/employees"
    roles_url = f"/api/v1/shops/{shop_id}/employee-roles"
    assert client.get(employees_url, headers=owner_headers).json()["items"][0]["role_name"] == "普通员工"
    assert client.get(employees_url, headers=applicant_headers).status_code == 403
    assert client.get(roles_url, headers=owner_headers).json()["total"] == 1
    created_role = client.post(roles_url, headers=owner_headers, json={"name": "收银"})
    assert created_role.status_code == 201, created_role.text
    assert client.post(roles_url, headers=owner_headers, json={"name": "收银"}).status_code == 409
    role_id = created_role.json()["id"]
    patched = client.patch(
        f"{employees_url}/{applicant_id}/role", headers=owner_headers, json={"role_id": role_id}
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["role_name"] == "收银"
    absent_user_id = 9223372036854775807
    assert client.delete(f"{employees_url}/{absent_user_id}", headers=owner_headers).status_code == 404
    assert client.patch(
        f"{employees_url}/{absent_user_id}/role", headers=owner_headers, json={"role_id": role_id}
    ).status_code == 404
    assert client.get("/api/v1/auth/me", headers=applicant_headers).json()["role"] == "SHOP_EMPLOYEE"
    with factory() as session:
        identity = IdentityService.get_identity(session, session.get(User, applicant_id))
        assert identity.role is Role.SHOP_EMPLOYEE
        assert AuthorizationService.check_shop(session, identity, shop_id) == merchant_id
        AuthorizationService.check_order_action(session, identity, shop_id=shop_id, action=OrderAction.CREATE)
        with pytest.raises(AccessDenied):
            AuthorizationService.check_shop_management(session, identity, shop_id)
    with factory.begin() as session:
        session.get(User, applicant_id).is_platform_admin = True
    with factory() as session:
        with pytest.raises(IdentityConflict):
            IdentityService.get_identity(session, session.get(User, applicant_id))
    assert client.get("/api/v1/auth/me", headers=applicant_headers).status_code == 403
    with factory.begin() as session:
        session.get(User, applicant_id).is_platform_admin = False
    restored = client.get("/api/v1/auth/me", headers=applicant_headers)
    assert restored.status_code == 200 and restored.json()["role"] == "SHOP_EMPLOYEE"
    with factory() as session:
        identity = IdentityService.get_identity(session, session.get(User, applicant_id))
        assert identity.role is Role.SHOP_EMPLOYEE

    other_applicant_id, other_applicant_headers = _account(client, "employee-second-applicant")
    other_application = client.post(
        "/api/v1/employees/applications", headers=other_applicant_headers, json={"shop_id": shop_id}
    ).json()
    assert client.post(
        f"/api/v1/employees/applications/{other_application['id']}/review", headers=applicant_headers,
        json={"decision": "REJECT", "rejection_reason": "No review authority"},
    ).status_code == 403

    removed = client.delete(f"{employees_url}/{applicant_id}", headers=owner_headers)
    assert removed.status_code == 204
    assert client.get("/api/v1/auth/me", headers=applicant_headers).json()["role"] == "NONE"
    assert client.patch(
        f"{employees_url}/{applicant_id}/role", headers=owner_headers, json={"role_id": role_id}
    ).status_code == 404
    reapplied = client.post(
        "/api/v1/employees/applications", headers=applicant_headers, json={"shop_id": shop_id}
    )
    assert reapplied.status_code == 201, reapplied.text
    with factory() as session:
        assert session.scalar(select(func.count(ShopEmployee.id)).where(ShopEmployee.user_id == applicant_id)) == 0
        assert session.scalar(select(func.count(AuditEvent.id)).where(
            AuditEvent.event_type == "shop_employee_removed",
            AuditEvent.target_type == "shop_employee",
            AuditEvent.target_id == employee_id,
        )) == 1
        assert session.scalar(select(func.count(AuditEvent.id)).where(
            AuditEvent.event_type == "shop_employee_role_created",
            AuditEvent.target_type == "shop_employee_role",
            AuditEvent.target_id == role_id,
        )) == 1
        assert session.scalar(select(func.count(AuditEvent.id)).where(
            AuditEvent.event_type == "shop_employee_role_changed",
            AuditEvent.target_type == "shop_employee",
            AuditEvent.target_id == employee_id,
        )) == 1


def test_employee_tenant_scope_and_only_applicant_or_shop_owner_can_read(auth_client):
    client, factory = auth_client
    owner_a, headers_a = _account(client, "scope-owner-a")
    owner_b, headers_b = _account(client, "scope-owner-b")
    shop_a, _, _ = _shop(factory, owner_a)
    shop_b, _, _ = _shop(factory, owner_b)
    applicant_a, applicant_headers_a = _account(client, "scope-applicant-a")
    applicant_b, applicant_headers_b = _account(client, "scope-applicant-b")
    app_a = client.post("/api/v1/employees/applications", headers=applicant_headers_a, json={"shop_id": shop_a}).json()
    app_b = client.post("/api/v1/employees/applications", headers=applicant_headers_b, json={"shop_id": shop_b}).json()
    assert client.get("/api/v1/employees/applications?shop_id=999999", headers=applicant_headers_a).json()["items"][0]["id"] == app_a["id"]
    assert client.get("/api/v1/employees/applications", headers=headers_a).json()["items"][0]["id"] == app_a["id"]
    assert client.post(
        f"/api/v1/employees/applications/{app_b['id']}/review", headers=headers_a,
        json={"decision": "REJECT", "rejection_reason": "other shop"},
    ).status_code == 403
    rejected = client.post(
        f"/api/v1/employees/applications/{app_b['id']}/review", headers=headers_b,
        json={"decision": "REJECT", "rejection_reason": "Application declined"},
    )
    assert rejected.status_code == 200, rejected.text
    assert rejected.json()["status"] == "REJECTED"
    assert rejected.json()["rejection_reason"] == "Application declined"
    assert client.get(f"/api/v1/shops/{shop_b}/employees", headers=headers_a).status_code == 403
    assert client.get(f"/api/v1/shops/{shop_b}/employee-roles", headers=headers_a).status_code == 403
    assert client.get(f"/api/v1/shops/{shop_a}/employees", headers=headers_b).status_code == 403
    assert client.get("/api/v1/employees/applications?limit=101", headers=applicant_headers_a).status_code == 422
    assert client.post(
        f"/api/v1/employees/applications/{app_a['id']}/review", headers=headers_a,
        json={"decision": "APPROVE"},
    ).status_code == 200
    shared_a = client.post(
        f"/api/v1/shops/{shop_a}/employee-roles", headers=headers_a, json={"name": "Shared"}
    )
    shared_b = client.post(
        f"/api/v1/shops/{shop_b}/employee-roles", headers=headers_b, json={"name": "Shared"}
    )
    assert shared_a.status_code == shared_b.status_code == 201
    assert client.patch(
        f"/api/v1/shops/{shop_a}/employees/{applicant_a}/role", headers=headers_a,
        json={"role_id": shared_b.json()["id"]},
    ).status_code == 404
    assert applicant_a != applicant_b


def test_employee_review_rechecks_identity_conflict_and_rejects_competing_approval(mysql_test_session_factory):
    factory = mysql_test_session_factory
    with factory.begin() as session:
        applicant = User(username=f"employee-race-app-{secrets.token_hex(4)}", email=f"{secrets.token_hex(5)}@test", password_hash="x")
        owner = User(username=f"employee-race-owner-{secrets.token_hex(4)}", email=f"{secrets.token_hex(5)}@test", password_hash="x")
        merchant_admin = User(username=f"employee-race-admin-{secrets.token_hex(4)}", email=f"{secrets.token_hex(5)}@test", password_hash="x")
        platform = User(username=f"employee-race-platform-{secrets.token_hex(4)}", email=f"{secrets.token_hex(5)}@test", password_hash="x", is_platform_admin=True)
        session.add_all([applicant, owner, merchant_admin, platform])
        session.flush()
        merchant = Merchant(name=f"Employee race merchant {secrets.token_hex(4)}", admin_user_id=merchant_admin.id)
        session.add(merchant)
        session.flush()
        shop = Shop(merchant_id=merchant.id, name=f"Employee race shop {secrets.token_hex(4)}", owner_user_id=owner.id)
        session.add(shop)
        session.flush()
        role = ShopEmployeeRole(shop_id=shop.id, name="普通员工")
        session.add(role)
        employee_app = EmployeeApplication(shop_id=shop.id, applicant_user_id=applicant.id)
        merchant_app = MerchantApplication(applicant_user_id=applicant.id, merchant_name=f"Competing merchant {secrets.token_hex(4)}")
        session.add_all([employee_app, merchant_app])
        session.flush()
        applicant_id, owner_id, platform_id = applicant.id, owner.id, platform.id
        shop_id, employee_app_id, merchant_app_id = shop.id, employee_app.id, merchant_app.id
    barrier = Barrier(3)

    def approve_employee():
        barrier.wait(timeout=10)
        with factory() as session:
            try:
                EmployeeService.review(
                    session, reviewer_user_id=owner_id, application_id=employee_app_id,
                    expected_applicant_user_id=applicant_id, expected_shop_id=shop_id,
                    expected_owner_user_id=owner_id, decision=EmployeeApplicationDecision.APPROVE,
                    rejection_reason=None,
                )
                return "employee"
            except EmployeeIneligible:
                return "conflict"

    def approve_merchant():
        from app.modules.merchants.schemas import ApplicationDecision
        from app.services.merchant_applications import ApplicationIneligible, MerchantApplicationService

        barrier.wait(timeout=10)
        with factory() as session:
            try:
                MerchantApplicationService.review(
                    session, reviewer_user_id=platform_id, application_id=merchant_app_id,
                    expected_applicant_user_id=applicant_id, decision=ApplicationDecision.APPROVE,
                    rejection_reason=None,
                )
                return "merchant"
            except ApplicationIneligible:
                return "conflict"

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(approve_employee), executor.submit(approve_merchant)]
        barrier.wait(timeout=10)
        results = [future.result(timeout=20) for future in futures]
    assert results.count("conflict") == 1
    assert results.count("employee") + results.count("merchant") == 1
    with factory() as session:
        merchant_identity = session.scalar(select(Merchant.id).where(Merchant.admin_user_id == applicant_id))
        employee_identity = session.scalar(select(ShopEmployee.id).where(ShopEmployee.user_id == applicant_id))
        assert bool(merchant_identity) != bool(employee_identity)


def test_employee_and_shop_owner_approvals_cannot_assign_two_shop_identities(mysql_test_session_factory):
    from app.modules.shops.schemas import ShopApplicationDecision
    from app.services.shop_applications import ShopApplicationIneligible, ShopApplicationService

    factory = mysql_test_session_factory
    with factory.begin() as session:
        applicant = User(username=f"employee-shop-race-app-{secrets.token_hex(4)}", email=f"{secrets.token_hex(5)}@test", password_hash="x")
        shop_owner = User(username=f"employee-shop-race-owner-{secrets.token_hex(4)}", email=f"{secrets.token_hex(5)}@test", password_hash="x")
        merchant_admin = User(username=f"employee-shop-race-admin-{secrets.token_hex(4)}", email=f"{secrets.token_hex(5)}@test", password_hash="x")
        session.add_all([applicant, shop_owner, merchant_admin])
        session.flush()
        merchant = Merchant(name=f"Employee shop race merchant {secrets.token_hex(4)}", admin_user_id=merchant_admin.id)
        session.add(merchant)
        session.flush()
        shop = Shop(merchant_id=merchant.id, name=f"Employee target {secrets.token_hex(4)}", owner_user_id=shop_owner.id)
        session.add(shop)
        session.flush()
        session.add(ShopEmployeeRole(shop_id=shop.id, name="普通员工"))
        employee_app = EmployeeApplication(shop_id=shop.id, applicant_user_id=applicant.id)
        shop_app = ShopApplication(merchant_id=merchant.id, applicant_user_id=applicant.id, shop_name=f"Owner target {secrets.token_hex(4)}")
        session.add_all([employee_app, shop_app])
        session.flush()
        applicant_id, owner_id, admin_id = applicant.id, shop_owner.id, merchant_admin.id
        merchant_id, shop_id = merchant.id, shop.id
        employee_app_id, shop_app_id = employee_app.id, shop_app.id
    barrier = Barrier(3)

    def approve_employee():
        barrier.wait(timeout=10)
        with factory() as session:
            try:
                EmployeeService.review(
                    session, reviewer_user_id=owner_id, application_id=employee_app_id,
                    expected_applicant_user_id=applicant_id, expected_shop_id=shop_id,
                    expected_owner_user_id=owner_id, decision=EmployeeApplicationDecision.APPROVE,
                    rejection_reason=None,
                )
                return "employee"
            except EmployeeIneligible:
                return "conflict"

    def approve_shop():
        barrier.wait(timeout=10)
        with factory() as session:
            try:
                ShopApplicationService.review(
                    session, reviewer_user_id=admin_id, application_id=shop_app_id,
                    expected_applicant_user_id=applicant_id, expected_merchant_id=merchant_id,
                    expected_admin_user_id=admin_id, decision=ShopApplicationDecision.APPROVE,
                    rejection_reason=None,
                )
                return "owner"
            except ShopApplicationIneligible:
                return "conflict"

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(approve_employee), executor.submit(approve_shop)]
        barrier.wait(timeout=10)
        results = [future.result(timeout=20) for future in futures]
    assert results.count("conflict") == 1
    assert results.count("employee") + results.count("owner") == 1
    with factory() as session:
        employee_identity = session.scalar(select(ShopEmployee.id).where(ShopEmployee.user_id == applicant_id))
        owner_identity = session.scalar(select(Shop.id).where(Shop.owner_user_id == applicant_id))
        assert bool(employee_identity) != bool(owner_identity)


def test_two_shops_cannot_approve_same_applicant_as_employee(mysql_test_session_factory):
    factory = mysql_test_session_factory
    with factory.begin() as session:
        applicant = User(username=f"two-employee-app-{secrets.token_hex(4)}", email=f"{secrets.token_hex(5)}@test", password_hash="x")
        owners = [
            User(username=f"two-employee-owner-{i}-{secrets.token_hex(4)}", email=f"{secrets.token_hex(5)}@test", password_hash="x")
            for i in range(2)
        ]
        admins = [
            User(username=f"two-employee-admin-{i}-{secrets.token_hex(4)}", email=f"{secrets.token_hex(5)}@test", password_hash="x")
            for i in range(2)
        ]
        session.add_all([applicant, *owners, *admins])
        session.flush()
        shops = []
        for index, (owner, admin) in enumerate(zip(owners, admins)):
            merchant = Merchant(name=f"Two employee merchant {index}-{secrets.token_hex(4)}", admin_user_id=admin.id)
            session.add(merchant)
            session.flush()
            shop = Shop(merchant_id=merchant.id, name=f"Two employee shop {index}-{secrets.token_hex(4)}", owner_user_id=owner.id)
            session.add(shop)
            session.flush()
            session.add(ShopEmployeeRole(shop_id=shop.id, name="普通员工"))
            application = EmployeeApplication(shop_id=shop.id, applicant_user_id=applicant.id)
            session.add(application)
            session.flush()
            shops.append((shop.id, owner.id, application.id))
        applicant_id = applicant.id
    barrier = Barrier(3)

    def approve(index):
        shop_id, owner_id, application_id = shops[index]
        barrier.wait(timeout=10)
        with factory() as session:
            try:
                EmployeeService.review(
                    session, reviewer_user_id=owner_id, application_id=application_id,
                    expected_applicant_user_id=applicant_id, expected_shop_id=shop_id,
                    expected_owner_user_id=owner_id, decision=EmployeeApplicationDecision.APPROVE,
                    rejection_reason=None,
                )
                return "approved"
            except EmployeeIneligible:
                return "ineligible"

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(approve, 0), executor.submit(approve, 1)]
        barrier.wait(timeout=10)
        results = [future.result(timeout=20) for future in futures]
    assert results.count("approved") == results.count("ineligible") == 1
    with factory() as session:
        applications = session.scalars(
            select(EmployeeApplication).where(EmployeeApplication.applicant_user_id == applicant_id)
        ).all()
        approved = [application for application in applications if application.status == "APPROVED"]
        pending = [application for application in applications if application.status == "PENDING"]
        assert len(approved) == 1 and len(pending) == 1
        assert session.scalar(select(func.count(ShopEmployee.id)).where(ShopEmployee.user_id == applicant_id)) == 1


def test_remove_and_role_update_race_never_recreates_membership(mysql_test_session_factory):
    factory = mysql_test_session_factory
    with factory.begin() as session:
        owner = User(username=f"remove-race-owner-{secrets.token_hex(4)}", email=f"{secrets.token_hex(5)}@test", password_hash="x")
        employee = User(username=f"remove-race-employee-{secrets.token_hex(4)}", email=f"{secrets.token_hex(5)}@test", password_hash="x")
        merchant_admin = User(username=f"remove-race-admin-{secrets.token_hex(4)}", email=f"{secrets.token_hex(5)}@test", password_hash="x")
        session.add_all([owner, employee, merchant_admin])
        session.flush()
        merchant = Merchant(name=f"Remove race merchant {secrets.token_hex(4)}", admin_user_id=merchant_admin.id)
        session.add(merchant)
        session.flush()
        shop = Shop(merchant_id=merchant.id, name=f"Remove race shop {secrets.token_hex(4)}", owner_user_id=owner.id)
        session.add(shop)
        session.flush()
        first_role = ShopEmployeeRole(shop_id=shop.id, name="普通员工")
        second_role = ShopEmployeeRole(shop_id=shop.id, name="主管")
        session.add_all([first_role, second_role])
        session.flush()
        membership = ShopEmployee(shop_id=shop.id, user_id=employee.id, role_id=first_role.id)
        session.add(membership)
        session.flush()
        owner_id, employee_id, shop_id, second_role_id = owner.id, employee.id, shop.id, second_role.id
    barrier = Barrier(3)

    def remove():
        barrier.wait(timeout=10)
        with factory() as session:
            EmployeeService.remove_employee(
                session, actor_user_id=owner_id, shop_id=shop_id, employee_user_id=employee_id,
                audit_writer=lambda **kwargs: True,
            )
            return "removed"

    def change_role():
        barrier.wait(timeout=10)
        with factory() as session:
            try:
                EmployeeService.update_employee_role(
                    session, actor_user_id=owner_id, shop_id=shop_id, employee_user_id=employee_id,
                    role_id=second_role_id, audit_writer=lambda **kwargs: True,
                )
                return "updated"
            except EmployeeNotFound:
                return "not_found"

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(remove), executor.submit(change_role)]
        barrier.wait(timeout=10)
        results = [future.result(timeout=20) for future in futures]
    assert "removed" in results and set(results) <= {"removed", "updated", "not_found"}
    with factory() as session:
        assert session.scalar(select(func.count(ShopEmployee.id)).where(ShopEmployee.user_id == employee_id)) == 0


def test_duplicate_submissions_for_one_user_serialize(mysql_test_session_factory):
    factory = mysql_test_session_factory
    with factory.begin() as session:
        owner = User(username=f"pending-owner-{secrets.token_hex(4)}", email=f"{secrets.token_hex(5)}@test", password_hash="x")
        owner_b = User(username=f"pending-owner-b-{secrets.token_hex(4)}", email=f"{secrets.token_hex(5)}@test", password_hash="x")
        applicant = User(username=f"pending-applicant-{secrets.token_hex(4)}", email=f"{secrets.token_hex(5)}@test", password_hash="x")
        admin = User(username=f"pending-admin-{secrets.token_hex(4)}", email=f"{secrets.token_hex(5)}@test", password_hash="x")
        admin_b = User(username=f"pending-admin-b-{secrets.token_hex(4)}", email=f"{secrets.token_hex(5)}@test", password_hash="x")
        session.add_all([owner, owner_b, applicant, admin, admin_b])
        session.flush()
        merchant = Merchant(name=f"Pending merchant {secrets.token_hex(4)}", admin_user_id=admin.id)
        session.add(merchant)
        session.flush()
        shop = Shop(merchant_id=merchant.id, name=f"Pending shop {secrets.token_hex(4)}", owner_user_id=owner.id)
        merchant_b = Merchant(name=f"Pending merchant b {secrets.token_hex(4)}", admin_user_id=admin_b.id)
        session.add_all([shop, merchant_b])
        session.flush()
        shop_b = Shop(merchant_id=merchant_b.id, name=f"Pending shop b {secrets.token_hex(4)}", owner_user_id=owner_b.id)
        session.add(shop_b)
        session.flush()
        session.add_all([
            ShopEmployeeRole(shop_id=shop.id, name="普通员工"),
            ShopEmployeeRole(shop_id=shop_b.id, name="普通员工"),
        ])
        targets = [(owner.id, shop.id), (owner_b.id, shop_b.id)]
        applicant_id = applicant.id
    barrier = Barrier(3)

    def submit(index):
        owner_id, shop_id = targets[index]
        barrier.wait(timeout=10)
        with factory() as session:
            try:
                EmployeeService.submit(
                    session, applicant_user_id=applicant_id, shop_id=shop_id,
                    expected_owner_user_id=owner_id,
                )
                return "submitted"
            except EmployeeConflict:
                return "conflict"

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(submit, 0), executor.submit(submit, 1)]
        barrier.wait(timeout=10)
        results = [future.result(timeout=20) for future in futures]
    assert results.count("submitted") == results.count("conflict") == 1
    with factory() as session:
        assert session.scalar(select(func.count(EmployeeApplication.id)).where(
            EmployeeApplication.applicant_user_id == applicant_id,
            EmployeeApplication.status == "PENDING",
        )) == 1


def test_concurrent_reviews_of_one_employee_application_transition_once(mysql_test_session_factory):
    factory = mysql_test_session_factory
    with factory.begin() as session:
        owner = User(username=f"review-owner-{secrets.token_hex(4)}", email=f"{secrets.token_hex(5)}@test", password_hash="x")
        applicant = User(username=f"review-applicant-{secrets.token_hex(4)}", email=f"{secrets.token_hex(5)}@test", password_hash="x")
        admin = User(username=f"review-admin-{secrets.token_hex(4)}", email=f"{secrets.token_hex(5)}@test", password_hash="x")
        session.add_all([owner, applicant, admin])
        session.flush()
        merchant = Merchant(name=f"Review merchant {secrets.token_hex(4)}", admin_user_id=admin.id)
        session.add(merchant)
        session.flush()
        shop = Shop(merchant_id=merchant.id, name=f"Review shop {secrets.token_hex(4)}", owner_user_id=owner.id)
        session.add(shop)
        session.flush()
        session.add(ShopEmployeeRole(shop_id=shop.id, name="普通员工"))
        application = EmployeeApplication(shop_id=shop.id, applicant_user_id=applicant.id)
        session.add(application)
        session.flush()
        owner_id, applicant_id, shop_id, application_id = owner.id, applicant.id, shop.id, application.id
    barrier = Barrier(3)

    def decide(decision):
        barrier.wait(timeout=10)
        with factory() as session:
            try:
                EmployeeService.review(
                    session, reviewer_user_id=owner_id, application_id=application_id,
                    expected_applicant_user_id=applicant_id, expected_shop_id=shop_id,
                    expected_owner_user_id=owner_id, decision=decision,
                    rejection_reason="Not this time" if decision is EmployeeApplicationDecision.REJECT else None,
                )
                return "reviewed"
            except EmployeeConflict:
                return "conflict"

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(decide, EmployeeApplicationDecision.APPROVE),
            executor.submit(decide, EmployeeApplicationDecision.REJECT),
        ]
        barrier.wait(timeout=10)
        results = [future.result(timeout=20) for future in futures]
    assert results.count("reviewed") == results.count("conflict") == 1
    with factory() as session:
        stored = session.get(EmployeeApplication, application_id)
        assert stored.status in {"APPROVED", "REJECTED"}
        employee_count = session.scalar(select(func.count(ShopEmployee.id)).where(ShopEmployee.user_id == applicant_id))
        assert employee_count == (1 if stored.status == "APPROVED" else 0)
        assert session.scalar(select(func.count(AuditEvent.id)).where(
            AuditEvent.event_type == "employee_application_reviewed",
            AuditEvent.target_id == application_id,
        )) == 1


def test_employee_audit_failure_does_not_undo_committed_application(mysql_test_session_factory):
    factory = mysql_test_session_factory
    with factory.begin() as session:
        owner = User(username=f"audit-owner-{secrets.token_hex(4)}", email=f"{secrets.token_hex(5)}@test", password_hash="x")
        applicant = User(username=f"audit-applicant-{secrets.token_hex(4)}", email=f"{secrets.token_hex(5)}@test", password_hash="x")
        merchant_admin = User(username=f"audit-admin-{secrets.token_hex(4)}", email=f"{secrets.token_hex(5)}@test", password_hash="x")
        session.add_all([owner, applicant, merchant_admin])
        session.flush()
        merchant = Merchant(name=f"Audit merchant {secrets.token_hex(4)}", admin_user_id=merchant_admin.id)
        session.add(merchant)
        session.flush()
        shop = Shop(merchant_id=merchant.id, name=f"Audit shop {secrets.token_hex(4)}", owner_user_id=owner.id)
        session.add(shop)
        session.flush()
        session.add(ShopEmployeeRole(shop_id=shop.id, name="普通员工"))
        session.flush()
        owner_id, applicant_id, shop_id = owner.id, applicant.id, shop.id
    with factory() as session:
        result = EmployeeService.submit(
            session, applicant_user_id=applicant_id, shop_id=shop_id,
            expected_owner_user_id=owner_id,
            audit_writer=lambda **kwargs: AuditService.record_after_commit(
                **kwargs, session_factory=lambda: (_ for _ in ()).throw(RuntimeError("audit unavailable"))
            ),
        )
        application_id = result.id
    with factory() as session:
        assert session.get(EmployeeApplication, application_id).status == "PENDING"
        assert session.scalar(select(func.count(AuditEvent.id)).where(
            AuditEvent.event_type == "employee_application_submitted",
            AuditEvent.target_id == application_id,
        )) == 0


def test_employee_approval_audit_failure_preserves_http_success(auth_client, monkeypatch, caplog):
    import app.services.audit as audit_module

    client, factory = auth_client
    owner_id, owner_headers = _account(client, "audit-http-owner")
    shop_id, _, _ = _shop(factory, owner_id)
    applicant_id, applicant_headers = _account(client, "audit-http-applicant")
    submitted = client.post(
        "/api/v1/employees/applications", headers=applicant_headers, json={"shop_id": shop_id}
    )
    assert submitted.status_code == 201, submitted.text

    def broken_factory():
        raise RuntimeError("private audit storage failure")

    monkeypatch.setattr(audit_module, "get_session_factory", broken_factory)
    reviewed = client.post(
        f"/api/v1/employees/applications/{submitted.json()['id']}/review", headers=owner_headers,
        json={"decision": "APPROVE"},
    )
    assert reviewed.status_code == 200, reviewed.text
    assert reviewed.json()["status"] == "APPROVED"
    assert "error_type=RuntimeError" in caplog.text
    assert "private audit storage failure" not in caplog.text
    with factory() as session:
        assert session.get(EmployeeApplication, submitted.json()["id"]).status == "APPROVED"
        assert session.scalar(select(func.count(ShopEmployee.id)).where(ShopEmployee.user_id == applicant_id)) == 1
        assert session.scalar(select(func.count(AuditEvent.id)).where(
            AuditEvent.event_type == "employee_application_reviewed",
            AuditEvent.target_id == submitted.json()["id"],
        )) == 0


def test_employee_approval_flush_failure_rolls_back_membership_application_and_audit(mysql_test_session_factory, monkeypatch):
    factory = mysql_test_session_factory
    with factory.begin() as session:
        owner = User(username=f"rollback-owner-{secrets.token_hex(4)}", email=f"{secrets.token_hex(5)}@test", password_hash="x")
        applicant = User(username=f"rollback-applicant-{secrets.token_hex(4)}", email=f"{secrets.token_hex(5)}@test", password_hash="x")
        merchant_admin = User(username=f"rollback-admin-{secrets.token_hex(4)}", email=f"{secrets.token_hex(5)}@test", password_hash="x")
        session.add_all([owner, applicant, merchant_admin])
        session.flush()
        merchant = Merchant(name=f"Rollback merchant {secrets.token_hex(4)}", admin_user_id=merchant_admin.id)
        session.add(merchant)
        session.flush()
        shop = Shop(merchant_id=merchant.id, name=f"Rollback shop {secrets.token_hex(4)}", owner_user_id=owner.id)
        session.add(shop)
        session.flush()
        session.add(ShopEmployeeRole(shop_id=shop.id, name="普通员工"))
        application = EmployeeApplication(shop_id=shop.id, applicant_user_id=applicant.id)
        session.add(application)
        session.flush()
        owner_id, applicant_id, shop_id, application_id = owner.id, applicant.id, shop.id, application.id

    with factory() as session:
        original_flush = session.flush
        def fail_after_flush(*args, **kwargs):
            original_flush(*args, **kwargs)
            raise RuntimeError("injected employee approval failure")
        monkeypatch.setattr(session, "flush", fail_after_flush)
        with pytest.raises(RuntimeError, match="injected employee approval failure"):
            EmployeeService.review(
                session, reviewer_user_id=owner_id, application_id=application_id,
                expected_applicant_user_id=applicant_id, expected_shop_id=shop_id,
                expected_owner_user_id=owner_id,
                decision=EmployeeApplicationDecision.APPROVE,
                rejection_reason=None,
            )
    with factory() as session:
        stored = session.get(EmployeeApplication, application_id)
        assert stored.status == "PENDING" and stored.reviewer_user_id is None
        assert session.scalar(select(func.count(ShopEmployee.id)).where(ShopEmployee.user_id == applicant_id)) == 0
        assert session.scalar(select(func.count(AuditEvent.id)).where(
            AuditEvent.event_type == "employee_application_reviewed", AuditEvent.target_id == application_id,
        )) == 0


@pytest.mark.parametrize(
    "event_type,target_type,target_id,outcome,event_data",
    [
        ("shop_employee_removed", "shop_employee", 1, "CREATED", {"shop_id": 1, "user_id": 1}),
        ("shop_employee_removed", "shop_employee", 1, "REMOVED", {"shop_id": 1}),
        ("shop_employee_role_created", "shop_employee_role", 1, "CREATED", {"password": "secret"}),
        ("shop_employee_role_changed", "shop_employee", 1, "UPDATED", {"shop_id": True, "role_id": 2}),
    ],
)
def test_employee_audit_rejects_wrong_outcomes_and_context(event_type, target_type, target_id, outcome, event_data):
    def forbidden_factory():
        raise AssertionError("invalid audit payload must not open a database session")

    assert not AuditService.record_after_commit(
        actor_user_id=1,
        event_type=event_type,
        target_type=target_type,
        target_id=target_id,
        outcome=outcome,
        event_data=event_data,
        session_factory=forbidden_factory,
    )
