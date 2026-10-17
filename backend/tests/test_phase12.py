import secrets
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import UUID

from sqlalchemy import select, text

from app.modules.employees.models import ShopEmployee, ShopEmployeeRole
from app.modules.inventory.models import Warehouse, WarehouseAccess
from app.modules.merchants.models import AuditEvent, Merchant
from app.modules.shops.models import Shop
from app.modules.users.models import User


def _account(client, label):
    key = secrets.token_hex(5)
    username = f"phase12-{label}-{key}"
    created = client.post("/api/v1/auth/register", json={
        "username": username, "email": f"{username}@example.com", "password": "long-correct-password",
    })
    assert created.status_code == 201, created.text
    token = client.post("/api/v1/auth/login", json={
        "identifier": username, "password": "long-correct-password",
    }).json()["access_token"]
    return created.json()["id"], {"Authorization": f"Bearer {token}"}


def _merchant_shop(factory, admin_id, owner_id, label, employee_id=None):
    with factory.begin() as session:
        merchant = Merchant(name=f"{label}-{secrets.token_hex(3)}", admin_user_id=admin_id)
        session.add(merchant)
        session.flush()
        session.add(Warehouse(merchant_id=merchant.id, name=f"{label} warehouse"))
        shop = Shop(merchant_id=merchant.id, name=f"{label} shop", owner_user_id=owner_id)
        session.add(shop)
        session.flush()
        role_id = None
        if employee_id is not None:
            role = ShopEmployeeRole(shop_id=shop.id, name="高级收银")
            session.add(role)
            session.flush()
            role_id = role.id
            session.add(ShopEmployee(shop_id=shop.id, user_id=employee_id, role_id=role.id))
        return merchant.id, shop.id, role_id


def _product(client, admin_headers):
    category = client.post("/api/v1/catalog/categories", headers=admin_headers,
                           json={"name": f"phase12 category {secrets.token_hex(3)}"})
    assert category.status_code == 201, category.text
    spu = client.post("/api/v1/catalog/spus", headers=admin_headers,
                      json={"category_id": category.json()["id"], "name": "phase12 product"})
    assert spu.status_code == 201, spu.text
    sku = client.post(f"/api/v1/catalog/spus/{spu.json()['id']}/skus", headers=admin_headers,
                      json={"sku_code": f"p12-{secrets.token_hex(3)}", "specification": "default", "price": "2.00"})
    assert sku.status_code == 201, sku.text
    published = client.patch(f"/api/v1/catalog/spus/{spu.json()['id']}/publish", headers=admin_headers)
    assert published.status_code == 200, published.text
    return sku.json()["id"]


def test_merchant_profile_and_full_owner_handoff_workflow(auth_client):
    client, factory = auth_client
    admin_id, admin_headers = _account(client, "merchant-admin")
    owner_id, owner_headers = _account(client, "owner")
    target_id, target_headers = _account(client, "employee-target")
    forced_target_id, forced_target_headers = _account(client, "force-target")
    merchant_id, shop_id, target_role_id = _merchant_shop(factory, admin_id, owner_id, "main", target_id)
    with factory.begin() as session:
        old_role = ShopEmployeeRole(shop_id=shop_id, name="原负责人岗位")
        session.add(old_role)
        session.flush()
        old_role_id = old_role.id
        session.get(Shop, shop_id).owner_restore_role_id = old_role.id

    renamed = client.patch("/api/v1/merchants/me", headers=admin_headers, json={"name": "  新商家名  "})
    assert renamed.status_code == 200 and renamed.json() == {"id": merchant_id, "name": "新商家名"}
    assert client.patch("/api/v1/merchants/me", headers=owner_headers, json={"name": "no"}).status_code == 403
    assert client.patch("/api/v1/merchants/me", headers=admin_headers,
                        json={"name": "no", "admin_user_id": owner_id}).status_code == 422
    assert client.patch("/api/v1/merchants/me", headers=admin_headers, json={"name": "   "}).status_code == 422

    proposed = client.put(f"/api/v1/shops/{shop_id}/owner", headers=owner_headers,
                          json={"action": "PROPOSE", "target_user_id": target_id})
    assert proposed.status_code == 200, proposed.text
    with factory.begin() as session:
        access = WarehouseAccess(shop_id=shop_id,
                                 warehouse_id=session.scalar(select(Warehouse.id).where(Warehouse.merchant_id == merchant_id)),
                                 merchant_id=merchant_id, granted_by_user_id=admin_id)
        session.add(access)
    proposal_id = proposed.json()["proposal_id"]
    UUID(proposal_id)
    assert proposed.json()["owner_user_id"] == owner_id
    assert client.get("/api/v1/auth/me", headers=target_headers).json()["role"] == "SHOP_EMPLOYEE"
    assert client.put(f"/api/v1/shops/{shop_id}/owner", headers=admin_headers,
                      json={"action": "CONFIRM", "proposal_id": proposal_id,
                            "from_owner_user_id": owner_id}).status_code == 422
    assert client.put(f"/api/v1/shops/{shop_id}/owner", headers=admin_headers,
                      json={"action": "CONFIRM", "proposal_id": "00000000-0000-0000-0000-000000000001"}).status_code == 409
    assert client.get(f"/api/v1/shops/{shop_id}", headers=owner_headers).status_code in (404, 405)

    confirmed = client.put(f"/api/v1/shops/{shop_id}/owner", headers=admin_headers,
                           json={"action": "CONFIRM", "proposal_id": proposal_id})
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["owner_user_id"] == target_id and confirmed.json()["status"] == "CONFIRMED"
    assert client.get("/api/v1/auth/me", headers=target_headers).json()["role"] == "SHOP_OWNER"
    old_identity = client.get("/api/v1/auth/me", headers=owner_headers).json()
    assert old_identity["role"] == "SHOP_EMPLOYEE" and old_identity["shop_id"] == shop_id
    with factory() as session:
        restored = session.scalar(select(ShopEmployee).where(ShopEmployee.shop_id == shop_id,
                                                              ShopEmployee.user_id == owner_id))
        assert restored is not None and restored.role_id == old_role_id
        promoted = session.scalar(select(ShopEmployee.id).where(ShopEmployee.user_id == target_id))
        assert promoted is None
        shop = session.get(Shop, shop_id)
        assert shop.owner_restore_role_id == target_role_id

    # Replaying consumed proposal is rejected; force requires its own reason and does not
    # restore the outgoing owner's saved employee access.
    assert client.put(f"/api/v1/shops/{shop_id}/owner", headers=admin_headers,
                      json={"action": "CONFIRM", "proposal_id": proposal_id}).status_code == 409
    assert client.put(f"/api/v1/shops/{shop_id}/owner", headers=admin_headers,
                      json={"action": "FORCE", "target_user_id": forced_target_id}).status_code == 422
    forced = client.put(f"/api/v1/shops/{shop_id}/owner", headers=admin_headers,
                        json={"action": "FORCE", "target_user_id": forced_target_id,
                              "reason": "  经核实的负责人离职  "})
    assert forced.status_code == 200, forced.text
    assert forced.json()["owner_user_id"] == forced_target_id and forced.json()["status"] == "FORCED"
    assert client.get("/api/v1/auth/me", headers=target_headers).json()["role"] == "NONE"
    with factory() as session:
        shop = session.get(Shop, shop_id)
        assert shop.last_owner_transfer_reason == "经核实的负责人离职"
        assert shop.last_owner_transfer_kind == "FORCE"
        assert shop.owner_restore_role_id is None
        assert session.scalar(select(ShopEmployee.id).where(ShopEmployee.user_id == target_id)) is None
        assert session.scalar(select(WarehouseAccess.id).where(WarehouseAccess.shop_id == shop_id)) is not None
        assert session.scalar(select(AuditEvent.id).where(
            AuditEvent.event_type == "shop_owner_transfer_forced", AuditEvent.target_id == shop_id
        )) is not None

    # The reason is retained as a business record but never exposed by the audit event API.
    owner_events = client.get("/api/v1/audit/events?event_type=shop_owner_transfer_forced",
                              headers=forced_target_headers)
    assert owner_events.status_code == 200, owner_events.text
    assert owner_events.json()["items"]
    assert "reason" not in owner_events.json()["items"][0]["event_data"]


def test_owner_consent_invalidation_force_scope_and_audit_tenant_filtering(auth_client):
    client, factory = auth_client
    admin_id, admin_headers = _account(client, "admin-a")
    other_admin_id, other_admin_headers = _account(client, "admin-b")
    owner_id, owner_headers = _account(client, "owner-a")
    new_owner_id, new_owner_headers = _account(client, "owner-target")
    other_owner_id, other_owner_headers = _account(client, "owner-b")
    platform_id, platform_headers = _account(client, "platform")
    merchant_id, shop_id, _role = _merchant_shop(factory, admin_id, owner_id, "tenant-a")
    other_merchant_id, other_shop_id, _ = _merchant_shop(factory, other_admin_id, other_owner_id, "tenant-b")
    with factory.begin() as session:
        session.get(User, platform_id).is_platform_admin = True

    proposal = client.put(f"/api/v1/shops/{shop_id}/owner", headers=owner_headers,
                         json={"action": "PROPOSE", "target_user_id": new_owner_id}).json()
    with factory.begin() as session:
        session.get(User, owner_id).is_active = False
    invalid = client.put(f"/api/v1/shops/{shop_id}/owner", headers=admin_headers,
                         json={"action": "CONFIRM", "proposal_id": proposal["proposal_id"]})
    assert invalid.status_code == 409
    with factory() as session:
        shop = session.get(Shop, shop_id)
        assert shop.pending_owner_proposal_id is None
        assert shop.pending_owner_target_user_id is None

    # A different shop owner is an incompatible identity, even inside the same merchant.
    assert client.put(f"/api/v1/shops/{shop_id}/owner", headers=admin_headers,
                      json={"action": "FORCE", "target_user_id": other_owner_id,
                            "reason": "must reject existing owner"}).status_code == 409

    # Force replacement can remove a disabled current owner but never grants the old employee role.
    force = client.put(f"/api/v1/shops/{shop_id}/owner", headers=admin_headers,
                       json={"action": "FORCE", "target_user_id": new_owner_id, "reason": "verified account closure"})
    assert force.status_code == 200, force.text
    assert client.get("/api/v1/auth/me", headers=owner_headers).status_code == 401
    assert client.put(f"/api/v1/shops/{other_shop_id}/owner", headers=admin_headers,
                      json={"action": "FORCE", "target_user_id": new_owner_id,
                            "reason": "cross merchant attempt"}).status_code == 403

    # A target_type collision and sensitive JSON fields cannot expand the tenant scope or output.
    with factory.begin() as session:
        session.add(AuditEvent(actor_user_id=admin_id, event_type="merchant_profile_updated", target_type="merchant",
                               target_id=merchant_id, outcome="UPDATED",
                               event_data={"jwt": "secret", "reason": "private", "password": "never"}))
        session.add(AuditEvent(actor_user_id=admin_id, event_type="order_created", target_type="merchant",
                               target_id=merchant_id, outcome="PENDING",
                               event_data={"merchant_id": merchant_id, "shop_id": shop_id}))
    own = client.get("/api/v1/audit/events?limit=100&offset=0", headers=admin_headers)
    other = client.get("/api/v1/audit/events?limit=100&offset=0", headers=other_admin_headers)
    platform = client.get("/api/v1/audit/events?limit=100&offset=0", headers=platform_headers)
    assert own.status_code == other.status_code == platform.status_code == 200
    assert all(row["target_id"] != other_shop_id for row in own.json()["items"])
    assert not any(row["event_type"] == "merchant_profile_updated" for row in other.json()["items"])
    assert not any(row["event_type"] == "merchant_profile_updated" for row in platform.json()["items"])
    assert not any(row["event_type"] == "order_created" and row["target_type"] == "merchant"
                   for row in own.json()["items"])
    profile = next(row for row in own.json()["items"] if row["event_type"] == "merchant_profile_updated")
    assert profile["event_data"] == {}
    assert client.get("/api/v1/audit/events?event_type=order_created&target_type=merchant", headers=admin_headers).json()["total"] == 0
    assert client.get("/api/v1/audit/events", headers=admin_headers, params={
        "start_at": "2026-10-17T00:00:00+08:00", "end_at": "2026-10-18T00:00:00Z",
    }).status_code == 422
    profile_created = next(row["created_at"] for row in own.json()["items"]
                           if row["event_type"] == "merchant_profile_updated")
    created_naive = profile_created
    for time_filter in ({"start_at": created_naive}, {"end_at": created_naive}):
        filtered = client.get("/api/v1/audit/events", headers=admin_headers,
                              params={**time_filter, "event_type": "merchant_profile_updated"})
        assert filtered.status_code == 200, filtered.text
        assert filtered.json()["total"] == 1, (profile_created, time_filter, filtered.json())
    for time_filter in (
        {"start_at": "2026-10-17T00:00:00+08:00"},
        {"end_at": "2026-10-18T00:00:00Z"},
        {"start_at": "2026-10-17T00:00:00+08:00", "end_at": "2026-10-18T00:00:00"},
    ):
        rejected = client.get("/api/v1/audit/events", headers=admin_headers, params=time_filter)
        assert rejected.status_code == 422
    assert client.get("/api/v1/audit/events", headers=owner_headers).status_code == 401
    assert client.get("/api/v1/audit/events", headers=other_owner_headers).json()["total"] == 0


def test_force_rejects_inactive_and_conflicting_target_identities(auth_client):
    client, factory = auth_client
    admin_id, admin_headers = _account(client, "eligibility-admin")
    owner_id, _ = _account(client, "eligibility-owner")
    platform_id, _ = _account(client, "eligibility-platform")
    other_admin_id, _ = _account(client, "eligibility-other-admin")
    other_employee_id, _ = _account(client, "eligibility-other-employee")
    inactive_id, _ = _account(client, "eligibility-inactive")
    conflicting_id, _ = _account(client, "eligibility-conflicting")
    _merchant_id, shop_id, _ = _merchant_shop(factory, admin_id, owner_id, "eligibility")
    other_merchant_id, other_shop_id, _ = _merchant_shop(
        factory, other_admin_id, _account(client, "eligibility-other-owner")[0], "eligibility-other",
        other_employee_id,
    )
    with factory.begin() as session:
        session.get(User, platform_id).is_platform_admin = True
        session.get(User, inactive_id).is_active = False
        session.get(User, conflicting_id).is_platform_admin = True
        other_shop = session.get(Shop, other_shop_id)
        other_role = ShopEmployeeRole(shop_id=other_shop.id, name="conflicting role")
        session.add(other_role)
        session.flush()
        session.add(ShopEmployee(shop_id=other_shop.id, user_id=conflicting_id, role_id=other_role.id))

    # Existing platform, merchant-admin, other-shop employee, inactive, and combined
    # platform/employee identities are not eligible owner targets.
    for target_id in (platform_id, other_admin_id, other_employee_id, inactive_id, conflicting_id):
        response = client.put(f"/api/v1/shops/{shop_id}/owner", headers=admin_headers,
                              json={"action": "FORCE", "target_user_id": target_id,
                                    "reason": "eligibility verification"})
        assert response.status_code == 409, (target_id, response.text)
    with factory() as session:
        assert session.scalar(select(Shop.owner_user_id).where(Shop.id == shop_id)) == owner_id


def test_owner_transfer_audit_failure_does_not_undo_committed_service_transaction(auth_client):
    client, factory = auth_client
    admin_id, _admin_headers = _account(client, "audit-failure-admin")
    owner_id, _owner_headers = _account(client, "audit-failure-owner")
    target_id, _target_headers = _account(client, "audit-failure-target")
    _merchant_id, shop_id, _ = _merchant_shop(factory, admin_id, owner_id, "audit-failure")
    from app.services.owner_transfers import OwnerTransferService

    with factory() as session:
        expected_owner, expected_merchant, expected_admin, proposal_ids = OwnerTransferService.prepare(session, shop_id)
        OwnerTransferService.end_read_only_transaction(session)

        def fail_audit(**_kwargs):
            raise RuntimeError("simulated audit storage outage; do not log this text")

        result = OwnerTransferService.execute(
            session, actor_user_id=admin_id, shop_id=shop_id, expected_owner_id=expected_owner,
            expected_merchant_id=expected_merchant, expected_admin_id=expected_admin,
            expected_proposal_ids=proposal_ids, action="FORCE", target_user_id=target_id,
            reason="business reason retained", audit_writer=fail_audit,
        )
        assert result["status"] == "FORCED"

    with factory() as session:
        shop = session.get(Shop, shop_id)
        assert shop.owner_user_id == target_id
        assert shop.last_owner_transfer_reason == "business reason retained"
        assert session.scalar(select(AuditEvent.id).where(
            AuditEvent.event_type == "shop_owner_transfer_forced", AuditEvent.target_id == shop_id
        )) is None


def test_mysql_competing_forced_transfers_serialize_on_root_user_and_shop(auth_client, monkeypatch):
    client, factory = auth_client
    admin_id, admin_headers = _account(client, "race-admin")
    owner_id, _owner_headers = _account(client, "race-owner")
    first_id, _ = _account(client, "race-first-target")
    second_id, _ = _account(client, "race-second-target")
    _merchant_id, shop_id, _ = _merchant_shop(factory, admin_id, owner_id, "race")
    barrier = Barrier(2)
    from app.services.owner_transfers import OwnerTransferService
    original_prepare = OwnerTransferService.prepare

    def synchronized_prepare(session, expected_shop_id, *, proposal_id=None):
        result = original_prepare(session, expected_shop_id, proposal_id=proposal_id)
        barrier.wait(timeout=5)
        return result

    monkeypatch.setattr(OwnerTransferService, "prepare", staticmethod(synchronized_prepare))

    def force(target_id):
        barrier.wait(timeout=5)
        return client.put(f"/api/v1/shops/{shop_id}/owner", headers=admin_headers,
                          json={"action": "FORCE", "target_user_id": target_id, "reason": "concurrent force"})

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(force, (first_id, second_id)))
    assert sorted(response.status_code for response in results) == [200, 409]
    with factory() as session:
        shop = session.get(Shop, shop_id)
        assert shop.owner_user_id in (first_id, second_id)
        assert session.scalar(select(Shop).where(Shop.id == shop_id)).owner_user_id == shop.owner_user_id


def test_mysql_employee_removal_and_owner_transfer_race_has_one_consistent_identity(auth_client):
    client, factory = auth_client
    admin_id, admin_headers = _account(client, "remove-race-admin")
    owner_id, owner_headers = _account(client, "remove-race-owner")
    employee_id, employee_headers = _account(client, "remove-race-employee")
    _merchant_id, shop_id, role_id = _merchant_shop(factory, admin_id, owner_id, "remove-race", employee_id)
    barrier = Barrier(2)

    def remove_employee():
        barrier.wait(timeout=5)
        return client.delete(f"/api/v1/shops/{shop_id}/employees/{employee_id}", headers=owner_headers)

    def promote_employee():
        barrier.wait(timeout=5)
        return client.put(f"/api/v1/shops/{shop_id}/owner", headers=admin_headers,
                          json={"action": "FORCE", "target_user_id": employee_id, "reason": "race transfer"})

    with ThreadPoolExecutor(max_workers=2) as pool:
        removed, promoted = list(pool.map(lambda fn: fn(), (remove_employee, promote_employee)))
    assert removed.status_code in (204, 403, 404)
    assert promoted.status_code == 200
    with factory() as session:
        shop = session.get(Shop, shop_id)
        employee = session.scalar(select(ShopEmployee).where(ShopEmployee.user_id == employee_id))
        assert shop.owner_user_id == employee_id
        assert employee is None
        assert shop.owner_restore_role_id in (None, role_id)


def test_mysql_database_failure_mid_confirmation_rolls_back_owner_and_employee_rows(auth_client):
    import pytest
    from sqlalchemy.exc import DBAPIError

    client, factory = auth_client
    admin_id, admin_headers = _account(client, "rollback-admin")
    owner_id, owner_headers = _account(client, "rollback-owner")
    target_id, target_headers = _account(client, "rollback-target")
    _merchant_id, shop_id, target_role_id = _merchant_shop(factory, admin_id, owner_id, "rollback", target_id)
    with factory.begin() as session:
        role = ShopEmployeeRole(shop_id=shop_id, name="old owner role")
        session.add(role)
        session.flush()
        session.get(Shop, shop_id).owner_restore_role_id = role.id
    proposed = client.put(f"/api/v1/shops/{shop_id}/owner", headers=owner_headers,
                          json={"action": "PROPOSE", "target_user_id": target_id})
    assert proposed.status_code == 200, proposed.text
    trigger = f"phase12_fail_employee_{secrets.token_hex(4)}"
    with factory.kw["bind"].begin() as connection:
        connection.execute(text(
            f"CREATE TRIGGER `{trigger}` BEFORE INSERT ON shop_employees FOR EACH ROW "
            "SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='phase12 injected transactional failure'"
        ))
    try:
        with pytest.raises(DBAPIError):
            client.put(f"/api/v1/shops/{shop_id}/owner", headers=admin_headers,
                       json={"action": "CONFIRM", "proposal_id": proposed.json()["proposal_id"]})
        with factory() as session:
            shop = session.get(Shop, shop_id)
            assert shop.owner_user_id == owner_id
            assert shop.pending_owner_proposal_id == proposed.json()["proposal_id"]
            assert shop.owner_restore_role_id == role.id
            assert session.scalar(select(ShopEmployee.role_id).where(ShopEmployee.user_id == target_id)) == target_role_id
            assert session.scalar(select(ShopEmployee.id).where(ShopEmployee.user_id == owner_id)) is None
    finally:
        with factory.kw["bind"].begin() as connection:
            connection.execute(text(f"DROP TRIGGER IF EXISTS `{trigger}`"))


def test_mysql_employee_approval_competes_safely_with_owner_promotion(auth_client):
    client, factory = auth_client
    admin_id, admin_headers = _account(client, "approval-race-admin")
    owner_id, owner_headers = _account(client, "approval-race-owner")
    applicant_id, applicant_headers = _account(client, "approval-race-applicant")
    _merchant_id, shop_id, _ = _merchant_shop(factory, admin_id, owner_id, "approval-race")
    application = client.post("/api/v1/employees/applications", headers=applicant_headers, json={"shop_id": shop_id})
    assert application.status_code == 201, application.text
    barrier = Barrier(2)

    def approve():
        barrier.wait(timeout=5)
        return client.post(f"/api/v1/employees/applications/{application.json()['id']}/review",
                           headers=owner_headers, json={"decision": "APPROVE"})

    def promote():
        barrier.wait(timeout=5)
        return client.put(f"/api/v1/shops/{shop_id}/owner", headers=admin_headers,
                          json={"action": "FORCE", "target_user_id": applicant_id, "reason": "approval race"})

    with ThreadPoolExecutor(max_workers=2) as pool:
        approved, promoted = list(pool.map(lambda fn: fn(), (approve, promote)))
    assert approved.status_code in (200, 403, 409)
    assert promoted.status_code == 200
    with factory() as session:
        assert session.scalar(select(Shop.owner_user_id).where(Shop.id == shop_id)) == applicant_id
        assert session.scalar(select(ShopEmployee.id).where(ShopEmployee.user_id == applicant_id)) is None


def test_mysql_order_creation_competes_safely_with_owner_transfer(auth_client):
    client, factory = auth_client
    admin_id, admin_headers = _account(client, "order-race-admin")
    owner_id, owner_headers = _account(client, "order-race-owner")
    target_id, _target_headers = _account(client, "order-race-target")
    merchant_id, shop_id, _ = _merchant_shop(factory, admin_id, owner_id, "order-race")
    with factory.begin() as session:
        warehouse_id = session.scalar(select(Warehouse.id).where(Warehouse.merchant_id == merchant_id))
        session.add(WarehouseAccess(shop_id=shop_id, warehouse_id=warehouse_id, merchant_id=merchant_id,
                                    granted_by_user_id=admin_id))
    sku_id = _product(client, admin_headers)
    seeded = client.post("/api/v1/inventory/operations", headers={**admin_headers, "Idempotency-Key": "p12-order-seed"},
                         json={"sku_id": sku_id, "delta": 3})
    assert seeded.status_code == 200, seeded.text
    barrier = Barrier(2)

    def create_order():
        barrier.wait(timeout=5)
        return client.post("/api/v1/orders", headers={**owner_headers, "Idempotency-Key": "p12-race-order"},
                           json={"items": [{"sku_id": sku_id, "quantity": 1}]})

    def transfer():
        barrier.wait(timeout=5)
        return client.put(f"/api/v1/shops/{shop_id}/owner", headers=admin_headers,
                          json={"action": "FORCE", "target_user_id": target_id, "reason": "order race"})

    with ThreadPoolExecutor(max_workers=2) as pool:
        order, transfer_result = list(pool.map(lambda fn: fn(), (create_order, transfer)))
    assert transfer_result.status_code == 200, transfer_result.text
    assert order.status_code in (201, 403, 409), order.text
    with factory() as session:
        assert session.scalar(select(Shop.owner_user_id).where(Shop.id == shop_id)) == target_id
        assert session.scalar(text("SELECT COUNT(*) FROM orders WHERE shop_id=:shop"), {"shop": shop_id}) == (1 if order.status_code == 201 else 0)
