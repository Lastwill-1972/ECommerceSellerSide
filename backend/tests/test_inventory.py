from concurrent.futures import ThreadPoolExecutor
import logging
import secrets

import pytest
from sqlalchemy import event, func, select
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import Session

from app.modules.catalog.models import Category, Spu, Sku
from app.modules.employees.models import ShopEmployee, ShopEmployeeRole
from app.modules.inventory.models import Stock, StockMovement, Warehouse, WarehouseAccess
from app.modules.merchants.models import AuditEvent, Merchant
from app.modules.shops.models import Shop
from app.modules.users.models import User
from app.services.inventory import InventoryConflict, InventoryService, MAX_BIGINT
from app.services.catalog import CatalogService


def _account(client, label):
    suffix = secrets.token_hex(5)
    username = f"inventory-{label}-{suffix}"
    created = client.post("/api/v1/auth/register", json={
        "username": username, "email": f"{username}@example.com", "password": "long-correct-password",
    })
    assert created.status_code == 201, created.text
    user_id = created.json()["id"]
    token = client.post("/api/v1/auth/login", json={
        "identifier": username, "password": "long-correct-password",
    }).json()["access_token"]
    return user_id, {"Authorization": f"Bearer {token}"}


def _merchant_product(client, factory, admin_id, admin_headers):
    with factory.begin() as session:
        merchant = Merchant(name=f"Inventory {secrets.token_hex(5)}", admin_user_id=admin_id)
        session.add(merchant)
        session.flush()
        warehouse = Warehouse(merchant_id=merchant.id, name="Shared warehouse")
        session.add(warehouse)
        session.flush()
        merchant_id, warehouse_id = merchant.id, warehouse.id
    category = client.post("/api/v1/catalog/categories", headers=admin_headers,
                           json={"name": f"Category {secrets.token_hex(4)}"})
    assert category.status_code == 201, category.text
    spu = client.post("/api/v1/catalog/spus", headers=admin_headers,
                      json={"category_id": category.json()["id"], "name": "Running shoe"})
    assert spu.status_code == 201, spu.text
    sku = client.post(f"/api/v1/catalog/spus/{spu.json()['id']}/skus", headers=admin_headers,
                      json={"sku_code": "RUN-40", "specification": "Size 40", "price": "10.00"})
    assert sku.status_code == 201, sku.text
    return merchant_id, warehouse_id, spu.json()["id"], sku.json()["id"]


def _shop(factory, merchant_id, owner_id, employee_id=None, *, authorized=False, warehouse_id=None, admin_id=None):
    with factory.begin() as session:
        shop = Shop(merchant_id=merchant_id, name=f"Shop {secrets.token_hex(4)}", owner_user_id=owner_id)
        session.add(shop)
        session.flush()
        if employee_id is not None:
            role = ShopEmployeeRole(shop_id=shop.id, name="自定义员工岗位")
            session.add(role)
            session.flush()
            session.add(ShopEmployee(shop_id=shop.id, user_id=employee_id, role_id=role.id))
        if authorized:
            session.add(WarehouseAccess(
                shop_id=shop.id, warehouse_id=warehouse_id, merchant_id=merchant_id,
                granted_by_user_id=admin_id,
            ))
        return shop.id


def test_inventory_api_permissions_shared_stock_and_idempotency(auth_client):
    client, factory = auth_client
    admin_id, admin_headers = _account(client, "admin")
    other_admin_id, other_admin_headers = _account(client, "other-admin")
    owner_id, owner_headers = _account(client, "owner")
    employee_id, employee_headers = _account(client, "employee")
    second_owner_id, second_owner_headers = _account(client, "second-owner")
    locked_owner_id, locked_owner_headers = _account(client, "locked-owner")
    platform_id, platform_headers = _account(client, "platform")
    with factory.begin() as session:
        session.get(User, platform_id).is_platform_admin = True
    merchant_id, warehouse_id, _spu_id, sku_id = _merchant_product(client, factory, admin_id, admin_headers)
    _shop(factory, merchant_id, owner_id, employee_id, authorized=True, warehouse_id=warehouse_id, admin_id=admin_id)
    _shop(factory, merchant_id, second_owner_id, authorized=True, warehouse_id=warehouse_id, admin_id=admin_id)
    _shop(factory, merchant_id, locked_owner_id)
    other_merchant_id, _other_wh, _other_spu, _other_sku = _merchant_product(
        client, factory, other_admin_id, other_admin_headers
    )

    initial = client.get("/api/v1/inventory/stocks", headers=admin_headers)
    assert initial.status_code == 200 and initial.json()["items"][0]["quantity"] == 0
    assert initial.json()["items"][0]["sku_id"] == sku_id
    warehouse_response = client.get("/api/v1/inventory/warehouse", headers=admin_headers)
    assert warehouse_response.status_code == 200 and warehouse_response.json()["merchant_id"] == merchant_id
    assert client.get("/api/v1/inventory/stocks", headers=locked_owner_headers).status_code == 403
    assert client.get("/api/v1/inventory/warehouse", headers=platform_headers).status_code == 403
    assert client.get("/api/v1/inventory/stocks", headers=platform_headers).status_code == 403
    assert client.get("/api/v1/inventory/movements", headers=employee_headers).status_code == 403
    assert client.get("/api/v1/inventory/movements", headers=owner_headers).status_code == 403

    for headers in (owner_headers, employee_headers, second_owner_headers):
        rows = client.get("/api/v1/inventory/stocks", headers=headers)
        assert rows.status_code == 200 and rows.json()["items"][0]["quantity"] == 0
        assert client.get("/api/v1/inventory/warehouse", headers=headers).status_code == 200

    first = client.post("/api/v1/inventory/operations", headers={**admin_headers, "Idempotency-Key": "stock-add-1"},
                        json={"sku_id": sku_id, "delta": 8})
    assert first.status_code == 200, first.text
    assert first.json()["quantity_before"] == 0 and first.json()["quantity_after"] == 8
    repeated = client.post("/api/v1/inventory/operations", headers={**admin_headers, "Idempotency-Key": "stock-add-1"},
                           json={"sku_id": sku_id, "delta": 8})
    assert repeated.status_code == 200 and repeated.json() == first.json()
    changed = client.post("/api/v1/inventory/operations", headers={**admin_headers, "Idempotency-Key": "stock-add-1"},
                          json={"sku_id": sku_id, "delta": 9})
    assert changed.status_code == 409
    for invalid in ({"sku_id": sku_id, "delta": 0}, {"sku_id": sku_id, "delta": True},
                    {"sku_id": sku_id, "delta": 1.5}):
        assert client.post("/api/v1/inventory/operations", headers={**admin_headers, "Idempotency-Key": "bad"},
                           json=invalid).status_code == 422
    assert client.post("/api/v1/inventory/operations", headers={**admin_headers, "Idempotency-Key": "bad key"},
                       json={"sku_id": sku_id, "delta": 1}).status_code == 409
    assert client.post("/api/v1/inventory/operations", headers={**admin_headers, "Idempotency-Key": "other-tenant"},
                       json={"sku_id": _other_sku, "delta": 1}).status_code == 404
    assert client.post("/api/v1/inventory/operations", headers={**employee_headers, "Idempotency-Key": "employee"},
                       json={"sku_id": sku_id, "delta": 1}).status_code == 403
    assert client.post("/api/v1/inventory/operations", headers={**other_admin_headers, "Idempotency-Key": "cross"},
                       json={"sku_id": sku_id, "delta": 1}).status_code == 404

    reduce = client.post("/api/v1/inventory/operations", headers={**admin_headers, "Idempotency-Key": "stock-remove-1"},
                         json={"sku_id": sku_id, "delta": -8})
    assert reduce.status_code == 200 and reduce.json()["quantity_after"] == 0
    assert client.post("/api/v1/inventory/operations", headers={**admin_headers, "Idempotency-Key": "stock-remove-1"},
                       json={"sku_id": sku_id, "delta": -8}).json() == reduce.json()
    assert client.post("/api/v1/inventory/operations", headers={**admin_headers, "Idempotency-Key": "negative"},
                       json={"sku_id": sku_id, "delta": -1}).status_code == 409

    with factory.begin() as session:
        stock = session.scalar(select(Stock).where(Stock.sku_id == sku_id))
        stock.quantity = MAX_BIGINT - 1
    max_adjustment = client.post(
        "/api/v1/inventory/operations", headers={**admin_headers, "Idempotency-Key": "reach-max"},
        json={"sku_id": sku_id, "delta": 1},
    )
    assert max_adjustment.status_code == 200 and max_adjustment.json()["quantity_after"] == MAX_BIGINT
    assert client.post("/api/v1/inventory/operations", headers={**admin_headers, "Idempotency-Key": "reach-max"},
                       json={"sku_id": sku_id, "delta": 1}).json() == max_adjustment.json()
    assert client.post("/api/v1/inventory/operations", headers={**admin_headers, "Idempotency-Key": "overflow"},
                       json={"sku_id": sku_id, "delta": 1}).status_code == 409

    movement_page = client.get("/api/v1/inventory/movements", headers=admin_headers)
    assert movement_page.status_code == 200 and movement_page.json()["total"] == 3
    assert client.get(f"/api/v1/inventory/movements?sku_id={sku_id}", headers=admin_headers).json()["total"] == 3
    assert client.get("/api/v1/inventory/stocks", headers=owner_headers).json()["items"][0]["quantity"] == MAX_BIGINT
    assert other_merchant_id != merchant_id
    with factory() as session:
        assert session.scalar(select(func.count(StockMovement.id))) == 3
        assert session.scalar(select(func.count(AuditEvent.id)).where(
            AuditEvent.event_type == "inventory_stock_adjusted"
        )) == 3


def test_inventory_concurrent_same_key_and_distinct_keys(auth_client):
    client, factory = auth_client
    admin_id, admin_headers = _account(client, "concurrent-admin")
    _merchant, _warehouse, _spu, sku_id = _merchant_product(client, factory, admin_id, admin_headers)

    def post(key, delta=1):
        return client.post("/api/v1/inventory/operations",
                           headers={**admin_headers, "Idempotency-Key": key},
                           json={"sku_id": sku_id, "delta": delta})

    with ThreadPoolExecutor(max_workers=6) as pool:
        same_key = list(pool.map(lambda _: post("same-concurrent-key"), range(6)))
    assert all(result.status_code == 200 for result in same_key), [r.text for r in same_key]
    assert len({result.json()["movement_id"] for result in same_key}) == 1
    with ThreadPoolExecutor(max_workers=8) as pool:
        different_keys = list(pool.map(lambda index: post(f"parallel-{index}"), range(8)))
    assert all(result.status_code == 200 for result in different_keys), [r.text for r in different_keys]
    with factory() as session:
        assert session.scalar(select(Stock.quantity).where(Stock.sku_id == sku_id)) == 9
        assert session.scalar(select(func.count(StockMovement.id)).where(StockMovement.sku_id == sku_id)) == 9


def test_inventory_sku_creation_adjustment_publish_and_sku_update_share_safe_lock_order(auth_client):
    client, factory = auth_client
    admin_id, admin_headers = _account(client, "lock-order-admin")
    _merchant, _warehouse, spu_id, sku_id = _merchant_product(client, factory, admin_id, admin_headers)

    requests = (
        lambda: client.post(f"/api/v1/catalog/spus/{spu_id}/skus", headers=admin_headers,
                            json={"sku_code": "RUN-41", "specification": "Size 41", "price": "11.00"}),
        lambda: client.post("/api/v1/inventory/operations",
                            headers={**admin_headers, "Idempotency-Key": "lock-order-adjust"},
                            json={"sku_id": sku_id, "delta": 2}),
        lambda: client.patch(f"/api/v1/catalog/skus/{sku_id}", headers=admin_headers,
                             json={"price": "12.00"}),
        lambda: client.patch(f"/api/v1/catalog/spus/{spu_id}/publish", headers=admin_headers),
    )
    with ThreadPoolExecutor(max_workers=4) as pool:
        responses = list(pool.map(lambda call: call(), requests))
    assert [response.status_code for response in responses] == [201, 200, 200, 200], [r.text for r in responses]
    with factory() as session:
        assert session.scalar(select(Stock.quantity).where(Stock.sku_id == sku_id)) == 2
        assert session.scalar(select(func.count(Stock.id)).where(Stock.merchant_id == _merchant)) == 2


def test_inventory_concurrent_competing_decrements_and_atomic_failures(auth_client, monkeypatch):
    client, factory = auth_client
    admin_id, admin_headers = _account(client, "rollback-admin")
    _merchant, _warehouse, _spu, sku_id = _merchant_product(client, factory, admin_id, admin_headers)
    with factory.begin() as session:
        session.scalar(select(Stock).where(Stock.sku_id == sku_id)).quantity = 3

    def decrement(index):
        return client.post("/api/v1/inventory/operations",
                           headers={**admin_headers, "Idempotency-Key": f"competing-decrement-{index}"},
                           json={"sku_id": sku_id, "delta": -1})

    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(decrement, range(6)))
    assert sum(result.status_code == 200 for result in results) == 3
    assert sum(result.status_code == 409 for result in results) == 3
    with factory() as session:
        assert session.scalar(select(Stock.quantity).where(Stock.sku_id == sku_id)) == 0
        assert session.scalar(select(func.count(StockMovement.id)).where(StockMovement.sku_id == sku_id)) == 3

    with factory() as session:
        original_flush = session.flush
        calls = 0

        def fail_after_movement(*args, **kwargs):
            nonlocal calls
            calls += 1
            original_flush(*args, **kwargs)
            if calls == 1:
                raise RuntimeError("injected after movement insert")

        monkeypatch.setattr(session, "flush", fail_after_movement)
        with pytest.raises(RuntimeError, match="injected after movement insert"):
            InventoryService.adjust(
                session, actor_user_id=admin_id, sku_id=sku_id, delta=1,
                idempotency_key="rollback-after-movement", audit_writer=lambda **_: True,
            )
    with factory() as session:
        assert session.scalar(select(Stock.quantity).where(Stock.sku_id == sku_id)) == 0
        assert session.scalar(select(func.count(StockMovement.id)).where(StockMovement.sku_id == sku_id)) == 3

    with factory() as session:
        result = InventoryService.adjust(
            session, actor_user_id=admin_id, sku_id=sku_id, delta=2,
            idempotency_key="audit-unavailable", audit_writer=lambda **_: False,
        )
        assert result.quantity_after == 2
    with factory() as session:
        assert session.scalar(select(Stock.quantity).where(Stock.sku_id == sku_id)) == 2
        assert session.scalar(select(func.count(StockMovement.id)).where(StockMovement.sku_id == sku_id)) == 4


def test_inventory_unique_conflict_fallback_reads_original_from_fresh_session(auth_client, monkeypatch):
    client, factory = auth_client
    admin_id, admin_headers = _account(client, "unique-fallback-admin")
    _merchant, _warehouse, _spu, sku_id = _merchant_product(client, factory, admin_id, admin_headers)
    first = client.post("/api/v1/inventory/operations",
                        headers={**admin_headers, "Idempotency-Key": "force-unique-fallback"},
                        json={"sku_id": sku_id, "delta": 1})
    assert first.status_code == 200
    with factory() as session:
        original_scalar = session.scalar

        def hide_existing_movement(statement, *args, **kwargs):
            if "stock_movements" in str(statement).lower():
                return None
            return original_scalar(statement, *args, **kwargs)

        monkeypatch.setattr(session, "scalar", hide_existing_movement)
        retry = InventoryService.adjust(session, actor_user_id=admin_id, sku_id=sku_id, delta=1,
                                        idempotency_key="force-unique-fallback")
        assert retry.model_dump() == first.json()
        assert not session.in_transaction()
    with factory() as session:
        assert session.scalar(select(Stock.quantity).where(Stock.sku_id == sku_id)) == 1
        assert session.scalar(select(func.count(StockMovement.id)).where(StockMovement.sku_id == sku_id)) == 1


def test_inventory_database_rejects_cross_tenant_stock_and_has_no_history_mutation_api(auth_client):
    client, factory = auth_client
    admin_id, admin_headers = _account(client, "constraint-admin")
    other_admin_id, other_admin_headers = _account(client, "constraint-other-admin")
    merchant_a, _warehouse_a, _spu_a, sku_a = _merchant_product(client, factory, admin_id, admin_headers)
    _merchant_b, warehouse_b, _spu_b, _sku_b = _merchant_product(client, factory, other_admin_id, other_admin_headers)
    with pytest.raises(IntegrityError):
        with factory.begin() as session:
            session.add(Stock(merchant_id=merchant_a, warehouse_id=warehouse_b, sku_id=sku_a, quantity=0))
            session.flush()
    schema = client.get("/openapi.json").json()
    assert "delete" not in schema["paths"]["/api/v1/inventory/movements"]
    assert "put" not in schema["paths"]["/api/v1/inventory/movements"]
    assert "patch" not in schema["paths"]["/api/v1/inventory/movements"]


def test_inventory_mysql_failures_roll_back_and_audit_failure_keeps_business_result(
    auth_client, monkeypatch, caplog,
):
    client, factory = auth_client
    admin_id, admin_headers = _account(client, "atomic-admin")
    _merchant, _warehouse, spu_id, sku_id = _merchant_product(client, factory, admin_id, admin_headers)
    engine = factory.kw["bind"]
    trigger_names = ("test_fail_stock_init", "test_fail_stock_update", "test_fail_movement_insert")
    with engine.begin() as connection:
        for name in trigger_names:
            connection.exec_driver_sql(f"DROP TRIGGER IF EXISTS `{name}`")
        connection.exec_driver_sql(
            "CREATE TRIGGER test_fail_stock_init BEFORE INSERT ON stocks FOR EACH ROW "
            "SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'injected stock init failure'"
        )
    try:
        with factory() as session:
            with pytest.raises(DBAPIError):
                CatalogService.create_sku(
                    session, actor_user_id=admin_id, spu_id=spu_id, sku_code="INIT-FAIL",
                    specification="Size 41", price=10,
                )
        with factory() as session:
            assert session.scalar(select(func.count(Sku.id)).where(Sku.sku_code == "INIT-FAIL")) == 0
            assert session.scalar(select(func.count(Stock.id)).where(Stock.sku_id == sku_id)) == 1
    finally:
        with engine.begin() as connection:
            connection.exec_driver_sql("DROP TRIGGER IF EXISTS test_fail_stock_init")

    with engine.begin() as connection:
        connection.exec_driver_sql(
            "CREATE TRIGGER test_fail_movement_insert BEFORE INSERT ON stock_movements FOR EACH ROW "
            "SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'injected movement failure'"
        )
    try:
        with factory() as session:
            with pytest.raises(DBAPIError):
                InventoryService.adjust(session, actor_user_id=admin_id, sku_id=sku_id,
                                        delta=5, idempotency_key="movement-db-failure")
        with factory() as session:
            assert session.scalar(select(Stock.quantity).where(Stock.sku_id == sku_id)) == 0
            assert session.scalar(select(func.count(StockMovement.id)).where(StockMovement.sku_id == sku_id)) == 0
    finally:
        with engine.begin() as connection:
            connection.exec_driver_sql("DROP TRIGGER IF EXISTS test_fail_movement_insert")

    with engine.begin() as connection:
        connection.exec_driver_sql(
            "CREATE TRIGGER test_fail_stock_update BEFORE UPDATE ON stocks FOR EACH ROW "
            "SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'injected stock update failure'"
        )
    try:
        with factory() as session:
            with pytest.raises(DBAPIError):
                InventoryService.adjust(session, actor_user_id=admin_id, sku_id=sku_id,
                                        delta=5, idempotency_key="stock-db-failure")
        with factory() as session:
            assert session.scalar(select(Stock.quantity).where(Stock.sku_id == sku_id)) == 0
            assert session.scalar(select(func.count(StockMovement.id)).where(StockMovement.sku_id == sku_id)) == 0
    finally:
        with engine.begin() as connection:
            connection.exec_driver_sql("DROP TRIGGER IF EXISTS test_fail_stock_update")

    # Run the actual independent AuditService while forcing its factory lookup to fail.
    import app.services.audit as audit_module

    def fail_audit_factory():
        raise RuntimeError("sensitive connection detail must not be logged")

    caplog.set_level(logging.ERROR, logger="app.services.audit")
    with factory() as session, monkeypatch.context() as patcher:
        patcher.setattr(audit_module, "get_session_factory", fail_audit_factory)
        result = InventoryService.adjust(session, actor_user_id=admin_id, sku_id=sku_id,
                                         delta=5, idempotency_key="audit-db-failure")
    assert result.quantity_after == 5
    assert "error_type=RuntimeError" in caplog.text
    assert "sensitive connection detail" not in caplog.text
    with factory() as session:
        assert session.scalar(select(Stock.quantity).where(Stock.sku_id == sku_id)) == 5
        assert session.scalar(select(func.count(StockMovement.id)).where(StockMovement.sku_id == sku_id)) == 1
