from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal, localcontext
import logging
import secrets
from threading import Barrier

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError

from app.modules.catalog.models import Sku, Spu
from app.modules.inventory.models import Stock, StockMovement
from app.modules.merchants.models import AuditEvent, Merchant
from app.modules.orders.models import Order, OrderItem
from app.modules.orders.schemas import OrderItemCreate
from app.modules.shops.models import Shop
from app.modules.users.models import User
from app.modules.inventory.models import Warehouse, WarehouseAccess
from app.modules.employees.models import ShopEmployee, ShopEmployeeRole
from app.services.inventory import MAX_BIGINT


def _account(client, label):
    suffix = secrets.token_hex(5)
    username = f"orders-{label}-{suffix}"
    created = client.post("/api/v1/auth/register", json={
        "username": username, "email": f"{username}@example.com", "password": "long-correct-password",
    })
    assert created.status_code == 201, created.text
    token = client.post("/api/v1/auth/login", json={
        "identifier": username, "password": "long-correct-password",
    }).json()["access_token"]
    return created.json()["id"], {"Authorization": f"Bearer {token}"}


def _merchant(factory, admin_id, label):
    with factory.begin() as session:
        merchant = Merchant(name=f"Order {label} {secrets.token_hex(4)}", admin_user_id=admin_id)
        session.add(merchant)
        session.flush()
        warehouse = Warehouse(merchant_id=merchant.id, name="Shared warehouse")
        session.add(warehouse)
        session.flush()
        return merchant.id, warehouse.id


def _shop(factory, merchant_id, owner_id, warehouse_id, admin_id, employee_id=None):
    with factory.begin() as session:
        shop = Shop(merchant_id=merchant_id, name=f"Order shop {secrets.token_hex(4)}", owner_user_id=owner_id)
        session.add(shop)
        session.flush()
        session.add(WarehouseAccess(shop_id=shop.id, warehouse_id=warehouse_id,
                                    merchant_id=merchant_id, granted_by_user_id=admin_id))
        if employee_id is not None:
            role = ShopEmployeeRole(shop_id=shop.id, name="自定义收银岗位")
            session.add(role)
            session.flush()
            session.add(ShopEmployee(shop_id=shop.id, user_id=employee_id, role_id=role.id))
        return shop.id


def _product(client, factory, admin_headers, price="10.00", name="Canvas shoe"):
    category = client.post("/api/v1/catalog/categories", headers=admin_headers,
                           json={"name": f"Category {secrets.token_hex(4)}"})
    assert category.status_code == 201, category.text
    spu = client.post("/api/v1/catalog/spus", headers=admin_headers,
                      json={"category_id": category.json()["id"], "name": name})
    assert spu.status_code == 201, spu.text
    sku = client.post(f"/api/v1/catalog/spus/{spu.json()['id']}/skus", headers=admin_headers,
                      json={"sku_code": f"SKU-{secrets.token_hex(3)}", "specification": "Size 40 / blue",
                            "price": price})
    assert sku.status_code == 201, sku.text
    published = client.patch(f"/api/v1/catalog/spus/{spu.json()['id']}/publish", headers=admin_headers)
    assert published.status_code == 200, published.text
    return spu.json()["id"], sku.json()["id"], sku.json()["sku_code"]


def _stock(factory, sku_id):
    with factory() as session:
        return session.scalar(select(Stock.quantity).where(Stock.sku_id == sku_id))


def test_manual_order_snapshot_idempotency_status_and_cancel_restore(auth_client):
    client, factory = auth_client
    admin_id, admin_headers = _account(client, "admin")
    owner_id, owner_headers = _account(client, "owner")
    employee_id, employee_headers = _account(client, "employee")
    other_owner_id, other_owner_headers = _account(client, "other-owner")
    merchant_id, warehouse_id = _merchant(factory, admin_id, "main")
    _shop(factory, merchant_id, owner_id, warehouse_id, admin_id, employee_id)
    other_shop_id = _shop(factory, merchant_id, other_owner_id, warehouse_id, admin_id)
    spu_id, sku_id, sku_code = _product(client, factory, admin_headers, name="Blue trainer")
    assert client.post("/api/v1/inventory/operations", headers={**admin_headers, "Idempotency-Key": "initial"},
                       json={"sku_id": sku_id, "delta": 10}).status_code == 200

    payload = {"items": [{"sku_id": sku_id, "quantity": 2}]}
    created = client.post("/api/v1/orders", headers={**owner_headers, "Idempotency-Key": "manual-001"}, json=payload)
    assert created.status_code == 201, created.text
    order = created.json()
    assert order["status"] == "PENDING" and order["total_amount"] == "20.00"
    assert order["items"][0]["unit_price"] == "10.00"
    assert order["items"][0]["sku_code_snapshot"] == sku_code
    assert order["items"][0]["spu_name_snapshot"] == "Blue trainer"
    assert _stock(factory, sku_id) == 8

    # Same key/hash returns the saved order even after mutable catalog state changes.
    with factory.begin() as session:
        sku = session.get(Sku, sku_id)
        sku.price = Decimal("99.99")
        session.get(Spu, spu_id).status = "OFF_SALE"
    retried = client.post("/api/v1/orders", headers={**owner_headers, "Idempotency-Key": "manual-001"}, json=payload)
    assert retried.status_code == 201 and retried.json() == order
    changed = client.post("/api/v1/orders", headers={**owner_headers, "Idempotency-Key": "manual-001"},
                          json={"items": [{"sku_id": sku_id, "quantity": 3}]})
    assert changed.status_code == 409
    assert client.post("/api/v1/orders", headers={**employee_headers, "Idempotency-Key": "manual-001"},
                       json=payload).status_code == 409
    assert _stock(factory, sku_id) == 8

    # Snapshot read remains independent from current SKU and SPU values.
    detail = client.get(f"/api/v1/orders/{order['id']}", headers=admin_headers)
    assert detail.status_code == 200 and detail.json()["items"][0]["unit_price"] == "10.00"
    assert detail.json()["items"][0]["spu_name_snapshot"] == "Blue trainer"
    assert client.get(f"/api/v1/orders/{order['id']}", headers=other_owner_headers).status_code == 404
    assert client.get(f"/api/v1/orders/{order['id']}", headers={}).status_code == 401
    assert client.get(f"/api/v1/orders?shop_id={other_shop_id}", headers=admin_headers).json()["total"] == 0
    assert client.post(f"/api/v1/orders/{order['id']}/complete", headers=admin_headers).status_code == 403
    assert client.post(f"/api/v1/orders/{order['id']}/cancel", headers=admin_headers).status_code == 403

    # Complete is one terminal transition and never changes the already-debited quantity.
    with factory.begin() as session:
        session.get(Spu, spu_id).status = "ON_SALE"
    second = client.post("/api/v1/orders", headers={**employee_headers, "Idempotency-Key": "manual-002"}, json=payload)
    assert second.status_code == 201, second.text
    before_complete = _stock(factory, sku_id)
    completed = client.post(f"/api/v1/orders/{second.json()['id']}/complete", headers=employee_headers)
    assert completed.status_code == 200 and completed.json()["status"] == "COMPLETED"
    assert _stock(factory, sku_id) == before_complete
    assert client.post(f"/api/v1/orders/{second.json()['id']}/cancel", headers=owner_headers).status_code == 409

    # Cancellation restores only PENDING quantities, exactly once.
    third = client.post("/api/v1/orders", headers={**owner_headers, "Idempotency-Key": "manual-003"}, json=payload)
    assert third.status_code == 201
    before_cancel = _stock(factory, sku_id)
    cancelled = client.post(f"/api/v1/orders/{third.json()['id']}/cancel", headers=owner_headers)
    assert cancelled.status_code == 200 and cancelled.json()["status"] == "CANCELLED"
    assert _stock(factory, sku_id) == before_cancel + 2
    assert client.post(f"/api/v1/orders/{third.json()['id']}/cancel", headers=employee_headers).status_code == 409
    with factory() as session:
        item_id = third.json()["items"][0]["id"]
        movements = session.scalars(select(StockMovement).where(StockMovement.order_item_id == item_id)).all()
        assert {(row.operation_type, row.quantity_delta) for row in movements} == {
            ("ORDER_DEDUCT", -2), ("ORDER_RESTORE", 2)
        }
        assert all(row.idempotency_key is None and row.request_hash is None for row in movements)
        assert session.scalar(select(func.count(AuditEvent.id)).where(AuditEvent.event_type == "order_created")) == 3
        assert session.scalar(select(func.count(AuditEvent.id)).where(AuditEvent.event_type == "order_completed")) == 1
        assert session.scalar(select(func.count(AuditEvent.id)).where(AuditEvent.event_type == "order_cancelled")) == 1


def test_order_validation_permissions_and_all_or_nothing_multisku(auth_client):
    client, factory = auth_client
    admin_id, admin_headers = _account(client, "admin")
    owner_id, owner_headers = _account(client, "owner")
    locked_owner_id, locked_headers = _account(client, "locked")
    platform_id, platform_headers = _account(client, "platform")
    merchant_id, warehouse_id = _merchant(factory, admin_id, "validation")
    shop_id = _shop(factory, merchant_id, owner_id, warehouse_id, admin_id)
    no_access_shop = _shop(factory, merchant_id, locked_owner_id, warehouse_id, admin_id)
    with factory.begin() as session:
        session.delete(session.scalar(select(WarehouseAccess).where(WarehouseAccess.shop_id == no_access_shop)))
        session.get(User, platform_id).is_platform_admin = True
    spu1, sku1, _ = _product(client, factory, admin_headers, name="First")
    spu2, sku2, _ = _product(client, factory, admin_headers, name="Second")
    other_admin_id, other_admin_headers = _account(client, "other-admin")
    other_merchant_id, other_warehouse_id = _merchant(factory, other_admin_id, "other")
    other_owner_id, _ = _account(client, "other-owner")
    _shop(factory, other_merchant_id, other_owner_id, other_warehouse_id, other_admin_id)
    _other_spu, other_sku, _ = _product(client, factory, other_admin_headers, name="Other tenant")
    assert client.post("/api/v1/inventory/operations", headers={**admin_headers, "Idempotency-Key": "seed-one"},
                       json={"sku_id": sku1, "delta": 3}).status_code == 200
    assert client.post("/api/v1/inventory/operations", headers={**admin_headers, "Idempotency-Key": "seed-two"},
                       json={"sku_id": sku2, "delta": 0}).status_code == 422

    for headers in (platform_headers, other_admin_headers, locked_headers):
        denied = client.post("/api/v1/orders", headers={**headers, "Idempotency-Key": "denied"},
                             json={"items": [{"sku_id": sku1, "quantity": 1}]})
        assert denied.status_code == 403
    assert client.get("/api/v1/orders", headers=platform_headers).status_code == 403
    assert client.get("/api/v1/orders", headers=other_admin_headers).status_code == 200
    assert client.get(f"/api/v1/orders?shop_id={shop_id}", headers=other_admin_headers).status_code == 403
    assert client.get("/api/v1/orders", headers=owner_headers).status_code == 200

    body = {"items": [{"sku_id": sku1, "quantity": 2}, {"sku_id": sku2, "quantity": 1}]}
    # Product 2 has zero stock; SKU 1 must not be partially debited.
    insufficient = client.post("/api/v1/orders", headers={**owner_headers, "Idempotency-Key": "short"}, json=body)
    assert insufficient.status_code == 409
    assert _stock(factory, sku1) == 3 and _stock(factory, sku2) == 0
    for invalid in (
        {"items": []}, {"items": [{"sku_id": sku1, "quantity": 1}, {"sku_id": sku1, "quantity": 1}]},
        {"items": [{"sku_id": sku1, "quantity": True}]},
        {"items": [{"sku_id": sku1, "quantity": 1.0}]},
        {"items": [{"sku_id": sku1, "quantity": "1"}]},
        {"items": [{"sku_id": other_sku, "quantity": 1}]},
        {"shop_id": shop_id, "items": [{"sku_id": sku1, "quantity": 1}]},
    ):
        assert client.post("/api/v1/orders", headers={**owner_headers, "Idempotency-Key": secrets.token_hex(4)},
                           json=invalid).status_code in (404, 409, 422)
    assert client.post("/api/v1/orders", headers=owner_headers, json={"items": [{"sku_id": sku1, "quantity": 1}]}).status_code == 422
    assert _stock(factory, sku1) == 3 and _stock(factory, sku2) == 0
    assert client.post("/api/v1/inventory/operations", headers={**admin_headers, "Idempotency-Key": "seed-second-sku"},
                       json={"sku_id": sku2, "delta": 1}).status_code == 200
    multisku = client.post("/api/v1/orders", headers={**owner_headers, "Idempotency-Key": "multi-success"}, json=body)
    assert multisku.status_code == 201, multisku.text
    assert len(multisku.json()["items"]) == 2 and multisku.json()["total_amount"] == "30.00"
    reverse_retry = client.post("/api/v1/orders", headers={**owner_headers, "Idempotency-Key": "multi-success"},
                               json={"items": list(reversed(body["items"]))})
    assert reverse_retry.status_code == 201 and reverse_retry.json() == multisku.json()
    assert _stock(factory, sku1) == 1 and _stock(factory, sku2) == 0


def test_mysql_orders_concurrent_shared_stock_and_distinct_shop_gap(auth_client):
    client, factory = auth_client
    admin_id, admin_headers = _account(client, "admin")
    owner_a_id, owner_a = _account(client, "owner-a")
    owner_b_id, owner_b = _account(client, "owner-b")
    merchant_id, warehouse_id = _merchant(factory, admin_id, "race")
    shop_a = _shop(factory, merchant_id, owner_a_id, warehouse_id, admin_id)
    shop_b = _shop(factory, merchant_id, owner_b_id, warehouse_id, admin_id)
    _spu, sku_id, _ = _product(client, factory, admin_headers, name="Limited edition")
    client.post("/api/v1/inventory/operations", headers={**admin_headers, "Idempotency-Key": "race-stock"},
                json={"sku_id": sku_id, "delta": 5})

    def place(headers, key):
        return client.post("/api/v1/orders", headers={**headers, "Idempotency-Key": key},
                           json={"items": [{"sku_id": sku_id, "quantity": 4}]})

    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda args: place(*args), [(owner_a, "race-a"), (owner_b, "race-b")]))
    assert sorted(response.status_code for response in responses) == [201, 409]
    assert _stock(factory, sku_id) == 1
    assert client.get(f"/api/v1/orders?shop_id={shop_b}", headers=owner_a).status_code == 403
    expected_shop_a_orders = 1 if responses[0].status_code == 201 else 0
    assert client.get(f"/api/v1/orders?shop_id={shop_a}", headers=owner_a).json()["total"] == expected_shop_a_orders

    # Opposite client item orders still lock SPU/SKU/Stock IDs canonically and
    # both requests finish with a complete two-item order.
    _spu_second, sku_second, _ = _product(client, factory, admin_headers, name="Second shared SKU")
    for sku, delta, key in ((sku_id, 2, "reverse-seed-one"), (sku_second, 3, "reverse-seed-two")):
        seeded = client.post("/api/v1/inventory/operations", headers={**admin_headers, "Idempotency-Key": key},
                             json={"sku_id": sku, "delta": delta})
        assert seeded.status_code == 200, seeded.text
    reverse_gate = Barrier(2)

    def place_two(headers, key, item_list):
        reverse_gate.wait(timeout=10)
        return client.post("/api/v1/orders", headers={**headers, "Idempotency-Key": key},
                           json={"items": item_list})

    first_items = [{"sku_id": sku_id, "quantity": 1}, {"sku_id": sku_second, "quantity": 1}]
    with ThreadPoolExecutor(max_workers=2) as pool:
        reversed_orders = list(pool.map(lambda args: place_two(*args), [
            (owner_a, "reverse-a", first_items), (owner_b, "reverse-b", list(reversed(first_items))),
        ]))
    assert all(response.status_code == 201 for response in reversed_orders), [r.text for r in reversed_orders]
    assert all(len(response.json()["items"]) == 2 for response in reversed_orders)

    # Different merchants have distinct warehouses. Start first order inserts in
    # parallel while their (shop_id,key) unique-index ranges are both empty.
    admin2_id, admin2_headers = _account(client, "admin2")
    owner2_id, owner2 = _account(client, "owner2")
    merchant2_id, warehouse2_id = _merchant(factory, admin2_id, "race-two")
    _shop(factory, merchant2_id, owner2_id, warehouse2_id, admin2_id)
    _spu2, sku2_id, _ = _product(client, factory, admin2_headers, name="Other limited edition")
    client.post("/api/v1/inventory/operations", headers={**admin2_headers, "Idempotency-Key": "race-stock-2"},
                json={"sku_id": sku2_id, "delta": 2})
    gate = Barrier(2)

    def first_order(headers, key, sku):
        gate.wait(timeout=10)
        return client.post("/api/v1/orders", headers={**headers, "Idempotency-Key": key},
                           json={"items": [{"sku_id": sku, "quantity": 1}]})

    with ThreadPoolExecutor(max_workers=2) as pool:
        unrelated = list(pool.map(lambda args: first_order(*args), [
            (owner_a, "brand-new-key-a", sku_id), (owner2, "brand-new-key-b", sku2_id),
        ]))
    assert [response.status_code for response in unrelated] == [201, 201], [r.text for r in unrelated]
    assert _stock(factory, sku_id) == 0 and _stock(factory, sku2_id) == 1


def test_order_big_decimal_precision_and_terminal_transition_race(auth_client):
    client, factory = auth_client
    admin_id, admin_headers = _account(client, "admin")
    owner_id, owner_headers = _account(client, "owner")
    employee_id, employee_headers = _account(client, "employee")
    merchant_id, warehouse_id = _merchant(factory, admin_id, "large")
    _shop(factory, merchant_id, owner_id, warehouse_id, admin_id, employee_id)
    _spu, sku_id, _ = _product(client, factory, admin_headers, price="9999999999.99", name="High value")
    seeded = client.post("/api/v1/inventory/operations", headers={**admin_headers, "Idempotency-Key": "large-stock"},
                         json={"sku_id": sku_id, "delta": MAX_BIGINT})
    assert seeded.status_code == 200 and seeded.json()["quantity_after"] == MAX_BIGINT
    large_order = client.post("/api/v1/orders", headers={**owner_headers, "Idempotency-Key": "large-order"},
                              json={"items": [{"sku_id": sku_id, "quantity": MAX_BIGINT}]})
    assert large_order.status_code == 201, large_order.text
    with localcontext() as ctx:
        ctx.prec = 50
        expected = Decimal("9999999999.99") * Decimal(MAX_BIGINT)
    assert Decimal(large_order.json()["total_amount"]) == expected
    assert Decimal(large_order.json()["items"][0]["subtotal"]) == expected
    assert _stock(factory, sku_id) == 0

    # Re-seed a small quantity and race terminal transitions on one PENDING order.
    client.post("/api/v1/inventory/operations", headers={**admin_headers, "Idempotency-Key": "small-stock"},
                json={"sku_id": sku_id, "delta": 3})
    pending = client.post("/api/v1/orders", headers={**owner_headers, "Idempotency-Key": "transition-race"},
                          json={"items": [{"sku_id": sku_id, "quantity": 2}]})
    assert pending.status_code == 201
    order_id = pending.json()["id"]
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda pair: client.post(
            f"/api/v1/orders/{order_id}/{pair[0]}", headers=pair[1]
        ), [("complete", owner_headers), ("cancel", employee_headers)]))
    assert sorted(response.status_code for response in results) == [200, 409]
    with factory() as session:
        order = session.get(Order, order_id)
        assert order.status in {"COMPLETED", "CANCELLED"}
        restores = session.scalar(select(func.count(StockMovement.id)).join(
            OrderItem, StockMovement.order_item_id == OrderItem.id
        ).where(OrderItem.order_id == order_id, StockMovement.operation_type == "ORDER_RESTORE"))
        assert restores == (1 if order.status == "CANCELLED" else 0)


def test_concurrent_double_cancel_and_cancel_overflow_leave_pending(auth_client):
    client, factory = auth_client
    admin_id, admin_headers = _account(client, "admin")
    owner_id, owner_headers = _account(client, "owner")
    employee_id, employee_headers = _account(client, "employee")
    merchant_id, warehouse_id = _merchant(factory, admin_id, "double-cancel")
    _shop(factory, merchant_id, owner_id, warehouse_id, admin_id, employee_id)
    _spu, sku_id, _ = _product(client, factory, admin_headers, name="Cancel race")
    client.post("/api/v1/inventory/operations", headers={**admin_headers, "Idempotency-Key": "cancel-seed"},
                json={"sku_id": sku_id, "delta": 6})
    pending = client.post("/api/v1/orders", headers={**owner_headers, "Idempotency-Key": "cancel-race"},
                          json={"items": [{"sku_id": sku_id, "quantity": 2}]})
    assert pending.status_code == 201
    order_id = pending.json()["id"]
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda headers: client.post(
            f"/api/v1/orders/{order_id}/cancel", headers=headers
        ), [owner_headers, employee_headers]))
    assert sorted(response.status_code for response in results) == [200, 409]
    assert _stock(factory, sku_id) == 6
    with factory() as session:
        assert session.scalar(select(func.count(StockMovement.id)).join(
            OrderItem, StockMovement.order_item_id == OrderItem.id
        ).where(OrderItem.order_id == order_id, StockMovement.operation_type == "ORDER_RESTORE")) == 1

    # If stock is independently raised to BIGINT max after a valid deduction,
    # cancellation must fail as a unit and leave both PENDING and no restore row.
    overflow_order = client.post("/api/v1/orders", headers={**owner_headers, "Idempotency-Key": "cancel-overflow"},
                                 json={"items": [{"sku_id": sku_id, "quantity": 2}]})
    assert overflow_order.status_code == 201 and _stock(factory, sku_id) == 4
    with factory.begin() as session:
        session.scalar(select(Stock).where(Stock.sku_id == sku_id)).quantity = MAX_BIGINT
    overflow_result = client.post(f"/api/v1/orders/{overflow_order.json()['id']}/cancel", headers=owner_headers)
    assert overflow_result.status_code == 409
    assert _stock(factory, sku_id) == MAX_BIGINT
    with factory() as session:
        order = session.get(Order, overflow_order.json()["id"])
        assert order.status == "PENDING"
        assert session.scalar(select(func.count(StockMovement.id)).join(
            OrderItem, StockMovement.order_item_id == OrderItem.id
        ).where(OrderItem.order_id == order.id, StockMovement.operation_type == "ORDER_RESTORE")) == 0


def test_order_races_manual_adjust_price_update_and_offshelf_approval(auth_client):
    client, factory = auth_client
    admin_id, admin_headers = _account(client, "admin")
    owner_id, owner_headers = _account(client, "owner")
    platform_id, platform_headers = _account(client, "platform")
    with factory.begin() as session:
        session.get(User, platform_id).is_platform_admin = True
    merchant_id, warehouse_id = _merchant(factory, admin_id, "catalog-races")
    _shop(factory, merchant_id, owner_id, warehouse_id, admin_id)
    spu_id, sku_id, _ = _product(client, factory, admin_headers, name="Racing price")
    client.post("/api/v1/inventory/operations", headers={**admin_headers, "Idempotency-Key": "race-seed"},
                json={"sku_id": sku_id, "delta": 5})

    gate = Barrier(2)

    def place_order():
        gate.wait(timeout=10)
        return client.post("/api/v1/orders", headers={**owner_headers, "Idempotency-Key": "price-race"},
                           json={"items": [{"sku_id": sku_id, "quantity": 1}]})

    def change_price():
        gate.wait(timeout=10)
        return client.patch(f"/api/v1/catalog/skus/{sku_id}", headers=admin_headers, json={"price": "20.00"})

    with ThreadPoolExecutor(max_workers=2) as pool:
        order_future = pool.submit(place_order)
        price_future = pool.submit(change_price)
        priced_order, price_update = order_future.result(timeout=20), price_future.result(timeout=20)
    assert priced_order.status_code == 201, priced_order.text
    assert price_update.status_code == 200, price_update.text
    assert priced_order.json()["items"][0]["unit_price"] in {"10.00", "20.00"}
    assert Decimal(priced_order.json()["total_amount"]) == Decimal(priced_order.json()["items"][0]["unit_price"])
    assert _stock(factory, sku_id) == 4

    # Manual adjustment and order purchase contend on the same Stock row.
    gate = Barrier(2)

    def buy_four():
        gate.wait(timeout=10)
        return client.post("/api/v1/orders", headers={**owner_headers, "Idempotency-Key": "manual-race"},
                           json={"items": [{"sku_id": sku_id, "quantity": 4}]})

    def adjust_minus_three():
        gate.wait(timeout=10)
        return client.post("/api/v1/inventory/operations",
                           headers={**admin_headers, "Idempotency-Key": "manual-race-adjust"},
                           json={"sku_id": sku_id, "delta": -3})

    with ThreadPoolExecutor(max_workers=2) as pool:
        order_future = pool.submit(buy_four)
        adjustment_future = pool.submit(adjust_minus_three)
        purchase, adjustment = order_future.result(timeout=20), adjustment_future.result(timeout=20)
    assert sorted([purchase.status_code, adjustment.status_code]) == [200, 409] or sorted(
        [purchase.status_code, adjustment.status_code]
    ) == [201, 409]
    assert _stock(factory, sku_id) in {0, 1}

    # Race a valid create with platform off-shelf approval. The SPU row lock makes
    # the winner define whether the order is accepted before the product leaves sale.
    _offshelf_spu, offshelf_sku, _ = _product(client, factory, admin_headers, name="Racing off-shelf")
    client.post("/api/v1/inventory/operations", headers={**admin_headers, "Idempotency-Key": "offseed"},
                json={"sku_id": offshelf_sku, "delta": 1})
    application = client.post("/api/v1/catalog/offshelf-applications", headers=admin_headers,
                              json={"spu_id": _offshelf_spu})
    assert application.status_code == 201, application.text
    gate = Barrier(2)

    def order_during_offshelf():
        gate.wait(timeout=10)
        return client.post("/api/v1/orders", headers={**owner_headers, "Idempotency-Key": "offshelf-race"},
                           json={"items": [{"sku_id": offshelf_sku, "quantity": 1}]})

    def approve_offshelf():
        gate.wait(timeout=10)
        return client.post(f"/api/v1/catalog/offshelf-applications/{application.json()['id']}/review",
                           headers=platform_headers, json={"decision": "APPROVE"})

    with ThreadPoolExecutor(max_workers=2) as pool:
        order_future = pool.submit(order_during_offshelf)
        approval_future = pool.submit(approve_offshelf)
        off_order, approval = order_future.result(timeout=20), approval_future.result(timeout=20)
    assert off_order.status_code in (201, 409), off_order.text
    assert approval.status_code == 200 and approval.json()["status"] == "APPROVED", approval.text
    with factory() as session:
        assert session.get(Spu, _offshelf_spu).status == "OFF_SALE"
    if off_order.status_code == 201:
        assert _stock(factory, offshelf_sku) == 0
    else:
        assert _stock(factory, offshelf_sku) == 1


def test_order_write_atomicity_and_independent_audit_failure(auth_client, caplog):
    client, factory = auth_client
    admin_id, admin_headers = _account(client, "admin")
    owner_id, owner_headers = _account(client, "owner")
    merchant_id, warehouse_id = _merchant(factory, admin_id, "failure")
    _shop(factory, merchant_id, owner_id, warehouse_id, admin_id)
    _spu, sku_id, _ = _product(client, factory, admin_headers, name="Transactional item")
    client.post("/api/v1/inventory/operations", headers={**admin_headers, "Idempotency-Key": "failure-seed"},
                json={"sku_id": sku_id, "delta": 5})

    def fail_movement(mapper, connection, target):
        raise RuntimeError("safe test fault")

    from sqlalchemy import event
    from app.services.orders import OrderService
    event.listen(StockMovement, "before_insert", fail_movement)
    try:
        with factory() as session, pytest.raises(RuntimeError, match="safe test fault"):
            OrderService.create(session, actor_user_id=owner_id,
                                items=[OrderItemCreate(sku_id=sku_id, quantity=2)],
                                idempotency_key="db-failure")
    finally:
        event.remove(StockMovement, "before_insert", fail_movement)
    assert _stock(factory, sku_id) == 5
    with factory() as session:
        assert session.scalar(select(func.count(Order.id))) == 0
        assert session.scalar(select(func.count(StockMovement.id)).where(StockMovement.operation_type == "ORDER_DEDUCT")) == 0

    # A real MySQL trigger failure after Order and OrderItem flushes still rolls
    # back the order rows, stock quantity update and all order movements.
    engine = factory.kw["bind"]
    trigger_name = f"fail_order_movement_{secrets.token_hex(4)}"
    with engine.begin() as connection:
        connection.execute(text(
            f"CREATE TRIGGER `{trigger_name}` BEFORE INSERT ON stock_movements "
            "FOR EACH ROW SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'test movement failure'"
        ))
    try:
        with factory() as session, pytest.raises(DBAPIError):
            OrderService.create(session, actor_user_id=owner_id,
                                items=[OrderItemCreate(sku_id=sku_id, quantity=2)],
                                idempotency_key="db-trigger-failure")
    finally:
        with engine.begin() as connection:
            connection.execute(text(f"DROP TRIGGER IF EXISTS `{trigger_name}`"))
    assert _stock(factory, sku_id) == 5
    with factory() as session:
        assert session.scalar(select(func.count(Order.id))) == 0
        assert session.scalar(select(func.count(StockMovement.id)).where(
            StockMovement.operation_type == "ORDER_DEDUCT"
        )) == 0

    from app.services.audit import AuditService

    def broken_audit_session_factory():
        raise RuntimeError("independent audit database unavailable")

    # The Service accepts an independent audit writer and catches raised failures
    # only after the order, item, movement and stock commit have completed.
    with factory() as session:
        result = OrderService.create(
            session, actor_user_id=owner_id,
            items=[OrderItemCreate(sku_id=sku_id, quantity=2)], idempotency_key="audit-failure",
            audit_writer=lambda **kwargs: AuditService.record_after_commit(
                **kwargs, session_factory=broken_audit_session_factory,
            ),
        )
    assert result.status == "PENDING" and _stock(factory, sku_id) == 3
    assert "audit write failed event_type=order_created" in caplog.text and "RuntimeError" in caplog.text

