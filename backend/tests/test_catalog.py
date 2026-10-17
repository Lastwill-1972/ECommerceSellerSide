from decimal import Decimal
import logging
import secrets

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError

from app.modules.catalog.models import Category, Spu, Sku
from app.modules.employees.models import ShopEmployee, ShopEmployeeRole
from app.modules.merchants.models import AuditEvent, Merchant
from app.modules.inventory.models import Warehouse
from app.modules.shops.models import Shop
from app.modules.users.models import User
from app.services.catalog import CatalogConflict, CatalogService


def _account(client, label):
    suffix = secrets.token_hex(5)
    username = f"catalog-{label}-{suffix}"
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


def _merchant(factory, admin_user_id, label):
    with factory.begin() as session:
        merchant = Merchant(name=f"Catalog {label} {secrets.token_hex(3)}", admin_user_id=admin_user_id)
        session.add(merchant)
        session.flush()
        session.add(Warehouse(merchant_id=merchant.id, name="Shared warehouse"))
        session.flush()
        return merchant.id


def _shop_and_employee(factory, merchant_id, owner_user_id, employee_user_id):
    with factory.begin() as session:
        shop = Shop(
            merchant_id=merchant_id, name=f"Catalog shop {secrets.token_hex(3)}", owner_user_id=owner_user_id
        )
        session.add(shop)
        session.flush()
        role = ShopEmployeeRole(shop_id=shop.id, name="普通员工")
        session.add(role)
        session.flush()
        session.add(ShopEmployee(shop_id=shop.id, user_id=employee_user_id, role_id=role.id))
        return shop.id


def test_catalog_api_tenant_roles_constraints_decimal_and_audit(auth_client):
    client, factory = auth_client
    admin_id, admin_headers = _account(client, "admin")
    other_admin_id, other_admin_headers = _account(client, "other-admin")
    owner_id, owner_headers = _account(client, "owner")
    other_same_owner_id, other_same_owner_headers = _account(client, "same-merchant-owner")
    other_same_member_id, _other_same_member_headers = _account(client, "same-merchant-member")
    employee_id, employee_headers = _account(client, "employee")
    other_owner_id, other_owner_headers = _account(client, "other-owner")
    other_employee_id, _other_employee_headers = _account(client, "other-employee")
    platform_id, platform_headers = _account(client, "platform")
    none_id, none_headers = _account(client, "none")
    with factory.begin() as session:
        session.get(User, platform_id).is_platform_admin = True
    merchant_id = _merchant(factory, admin_id, "one")
    other_merchant_id = _merchant(factory, other_admin_id, "two")
    shop_id = _shop_and_employee(factory, merchant_id, owner_id, employee_id)
    _same_merchant_shop_id = _shop_and_employee(
        factory, merchant_id, other_same_owner_id, other_same_member_id
    )
    _other_shop_id = _shop_and_employee(factory, other_merchant_id, other_owner_id, other_employee_id)

    assert client.get("/api/v1/catalog/categories", headers=none_headers).status_code == 403
    assert client.get("/api/v1/catalog/categories", headers=platform_headers).status_code == 403
    assert client.post("/api/v1/catalog/categories", headers=owner_headers, json={"name": "x"}).status_code == 403
    assert client.post("/api/v1/catalog/categories", headers=employee_headers, json={"name": "x"}).status_code == 403
    assert client.post("/api/v1/catalog/categories", headers=platform_headers, json={"name": "x"}).status_code == 403

    category = client.post(
        "/api/v1/catalog/categories", headers=admin_headers, json={"name": "  Shoes  "}
    )
    assert category.status_code == 201, category.text
    assert set(category.json()) == {"id", "merchant_id", "name", "created_at"}
    assert category.json()["merchant_id"] == merchant_id and category.json()["name"] == "Shoes"
    assert client.post(
        "/api/v1/catalog/categories", headers=admin_headers, json={"name": "shoes"}
    ).status_code == 409
    other_category = client.post(
        "/api/v1/catalog/categories", headers=other_admin_headers, json={"name": "Shoes"}
    )
    assert other_category.status_code == 201
    assert other_category.json()["merchant_id"] == other_merchant_id
    assert client.post(
        "/api/v1/catalog/categories", headers=admin_headers,
        json={"name": "Spoof", "merchant_id": other_merchant_id},
    ).status_code == 422

    category_list = client.get(
        f"/api/v1/catalog/categories?merchant_id={other_merchant_id}", headers=admin_headers
    )
    assert category_list.status_code == 200
    assert {row["id"] for row in category_list.json()["items"]} == {category.json()["id"]}
    for headers in (owner_headers, employee_headers):
        assert client.get("/api/v1/catalog/categories", headers=headers).json()["total"] == 1

    cross_category = client.post(
        "/api/v1/catalog/spus", headers=admin_headers,
        json={"category_id": other_category.json()["id"], "name": "Wrong tenant"},
    )
    assert cross_category.status_code == 404
    created = client.post(
        "/api/v1/catalog/spus", headers=admin_headers,
        json={"category_id": category.json()["id"], "name": "Sneaker", "description": "Everyday"},
    )
    assert created.status_code == 201, created.text
    spu = created.json()
    assert spu["status"] == "OFF_SALE" and spu["merchant_id"] == merchant_id
    assert set(spu) == {
        "id", "merchant_id", "category_id", "name", "description", "status", "created_at", "updated_at"
    }
    assert client.post(
        "/api/v1/catalog/spus", headers=admin_headers,
        json={"category_id": category.json()["id"], "name": "Invalid status", "status": "ON_SALE"},
    ).status_code == 422
    assert client.post(
        "/api/v1/catalog/spus", headers=admin_headers,
        json={"category_id": category.json()["id"], "name": "Spoof", "merchant_id": other_merchant_id},
    ).status_code == 422
    assert client.patch(
        f"/api/v1/catalog/spus/{spu['id']}", headers=admin_headers, json={"status": "ON_SALE"}
    ).status_code == 422
    assert client.patch(
        f"/api/v1/catalog/spus/{spu['id']}", headers=admin_headers,
        json={"category_id": other_category.json()["id"]},
    ).status_code == 404

    assert client.patch(f"/api/v1/catalog/spus/{spu['id']}/publish", headers=admin_headers).status_code == 409
    for bad_price in (-0.01, "NaN", "Infinity", "10000000000.00", "1.001"):
        invalid = client.post(
            f"/api/v1/catalog/spus/{spu['id']}/skus", headers=admin_headers,
            json={"sku_code": f"bad-{str(bad_price)}", "specification": "Size 40", "price": bad_price},
        )
        assert invalid.status_code == 422, (bad_price, invalid.text)
    sku = client.post(
        f"/api/v1/catalog/spus/{spu['id']}/skus", headers=admin_headers,
        json={"sku_code": "SNK-40", "specification": "Size 40; manual entry", "price": "9999999999.99"},
    )
    assert sku.status_code == 201, sku.text
    assert sku.json()["price"] == "9999999999.99"
    duplicate = client.post(
        f"/api/v1/catalog/spus/{spu['id']}/skus", headers=admin_headers,
        json={"sku_code": "SNK-40", "specification": "Size 41", "price": "0.00"},
    )
    assert duplicate.status_code == 409

    second_spu = client.post(
        "/api/v1/catalog/spus", headers=admin_headers,
        json={"category_id": category.json()["id"], "name": "Second sneaker"},
    )
    assert second_spu.status_code == 201
    same_code_other_spu = client.post(
        f"/api/v1/catalog/spus/{second_spu.json()['id']}/skus", headers=admin_headers,
        json={"sku_code": "SNK-40", "specification": "Size 40", "price": "10.00"},
    )
    assert same_code_other_spu.status_code == 201
    same_spu_second_sku = client.post(
        f"/api/v1/catalog/spus/{spu['id']}/skus", headers=admin_headers,
        json={"sku_code": "SNK-41", "specification": "Size 41", "price": "10.00"},
    )
    assert same_spu_second_sku.status_code == 201
    conflicting_code = client.patch(
        f"/api/v1/catalog/skus/{same_spu_second_sku.json()['id']}", headers=admin_headers,
        json={"sku_code": "SNK-40"},
    )
    assert conflicting_code.status_code == 409
    with factory() as session:
        assert session.get(Sku, same_spu_second_sku.json()["id"]).sku_code == "SNK-41"
    for invalid_price in (None, "-0.01", "1.001"):
        invalid_patch = client.patch(
            f"/api/v1/catalog/skus/{sku.json()['id']}", headers=admin_headers,
            json={"price": invalid_price},
        )
        assert invalid_patch.status_code == 422

    published = client.patch(f"/api/v1/catalog/spus/{spu['id']}/publish", headers=admin_headers)
    assert published.status_code == 200, published.text
    assert published.json()["status"] == "ON_SALE"
    assert client.patch(
        f"/api/v1/catalog/spus/{spu['id']}", headers=admin_headers, json={"name": "Updated Sneaker"}
    ).json()["name"] == "Updated Sneaker"
    assert client.patch(
        f"/api/v1/catalog/skus/{sku.json()['id']}", headers=admin_headers, json={"price": "17.25"}
    ).json()["price"] == "17.25"
    assert client.patch(
        f"/api/v1/catalog/skus/{sku.json()['id']}", headers=admin_headers, json={"spu_id": spu["id"]}
    ).status_code == 422

    for headers in (owner_headers, employee_headers, other_same_owner_headers):
        assert client.get(f"/api/v1/catalog/spus/{spu['id']}", headers=headers).json()["skus"][0]["price"] == "17.25"
        assert client.get("/api/v1/catalog/spus?status=ON_SALE", headers=headers).json()["total"] == 1
        assert client.patch(f"/api/v1/catalog/spus/{spu['id']}", headers=headers, json={"name": "forbidden"}).status_code == 403
        write_attempts = (
            client.post("/api/v1/catalog/categories", headers=headers, json={"name": "No write"}),
            client.post("/api/v1/catalog/spus", headers=headers, json={"category_id": category.json()["id"], "name": "No write"}),
            client.patch(f"/api/v1/catalog/spus/{spu['id']}", headers=headers, json={"name": "No write"}),
            client.patch(f"/api/v1/catalog/spus/{spu['id']}/publish", headers=headers),
            client.post(f"/api/v1/catalog/spus/{spu['id']}/skus", headers=headers, json={"sku_code": "NO", "specification": "manual", "price": "1.00"}),
            client.patch(f"/api/v1/catalog/skus/{sku.json()['id']}", headers=headers, json={"price": "1.00"}),
        )
        assert all(response.status_code == 403 for response in write_attempts)
    assert all(response.status_code == 403 for response in (
        client.post("/api/v1/catalog/categories", headers=platform_headers, json={"name": "No write"}),
        client.post("/api/v1/catalog/spus", headers=platform_headers, json={"category_id": category.json()["id"], "name": "No write"}),
        client.patch(f"/api/v1/catalog/spus/{spu['id']}", headers=platform_headers, json={"name": "No write"}),
        client.patch(f"/api/v1/catalog/spus/{spu['id']}/publish", headers=platform_headers),
        client.post(f"/api/v1/catalog/spus/{spu['id']}/skus", headers=platform_headers, json={"sku_code": "NO", "specification": "manual", "price": "1.00"}),
        client.patch(f"/api/v1/catalog/skus/{sku.json()['id']}", headers=platform_headers, json={"price": "1.00"}),
    ))
    assert client.patch(
        f"/api/v1/catalog/spus/{spu['id']}", headers=other_admin_headers, json={"name": "cross tenant"}
    ).status_code == 404
    assert client.post(
        f"/api/v1/catalog/spus/{spu['id']}/skus", headers=other_admin_headers,
        json={"sku_code": "CROSS", "specification": "manual", "price": "1.00"},
    ).status_code == 404
    assert client.patch(
        f"/api/v1/catalog/skus/{sku.json()['id']}", headers=platform_headers, json={"price": "1.00"}
    ).status_code == 403
    details = client.get(f"/api/v1/catalog/spus/{spu['id']}", headers=admin_headers)
    assert details.status_code == 200
    assert set(details.json()["skus"][0]) == {
        "id", "spu_id", "sku_code", "specification", "price", "created_at", "updated_at"
    }
    assert client.get(f"/api/v1/catalog/spus/{spu['id']}", headers=other_admin_headers).status_code == 404
    assert client.patch(
        f"/api/v1/catalog/skus/{sku.json()['id']}", headers=other_admin_headers, json={"price": "3.00"}
    ).status_code == 404
    assert client.get(f"/api/v1/catalog/spus/{spu['id']}", headers=none_headers).status_code == 403
    assert client.get(f"/api/v1/catalog/spus/{spu['id']}", headers=platform_headers).status_code == 403

    with factory() as session:
        db_sku = session.get(Sku, sku.json()["id"])
        assert db_sku.price == Decimal("17.25")
        assert session.scalar(select(func.count(AuditEvent.id)).where(
            AuditEvent.target_type == "category", AuditEvent.target_id == category.json()["id"]
        )) == 1
        assert session.scalar(select(func.count(AuditEvent.id)).where(
            AuditEvent.event_type == "catalog_spu_published", AuditEvent.target_id == spu["id"]
        )) == 1


def test_catalog_audit_failure_keeps_business_commit_and_category_duplicate_rolls_back(auth_client, monkeypatch, caplog):
    client, factory = auth_client
    admin_id, headers = _account(client, "service-admin")
    merchant_id = _merchant(factory, admin_id, "service")
    class AuditCommitFailure:
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
                        raise RuntimeError("private audit database failure")
                    return False
            return Transaction()

    monkeypatch.setattr("app.services.audit.get_session_factory", lambda: lambda: AuditCommitFailure())
    caplog.set_level(logging.ERROR, logger="app.services.audit")
    response = client.post("/api/v1/catalog/categories", headers=headers, json={"name": "Audit failure"})
    assert response.status_code == 201, response.text
    assert "error_type=RuntimeError" in caplog.text
    assert "private audit database failure" not in caplog.text
    with factory() as session:
        assert session.scalar(select(func.count(Category.id)).where(
            Category.merchant_id == merchant_id, Category.name == "Audit failure"
        )) == 1

    with factory() as session:
        result = CatalogService.create_category(
            session, actor_user_id=admin_id, name="Durable",
            audit_writer=lambda **_kwargs: False,
        )
        assert result.merchant_id == merchant_id
        with pytest.raises(CatalogConflict):
            CatalogService.create_category(session, actor_user_id=admin_id, name="Durable")
        assert session.scalar(select(func.count(Category.id)).where(
            Category.merchant_id == merchant_id, Category.name == "Durable"
        )) == 1


def test_catalog_composite_fk_rejects_cross_merchant_category(mysql_test_session_factory):
    factory = mysql_test_session_factory
    with factory.begin() as session:
        admins = [
            User(username=f"cat-fk-{secrets.token_hex(4)}", email=f"{secrets.token_hex(5)}@example.test", password_hash="x"),
            User(username=f"cat-fk-{secrets.token_hex(4)}", email=f"{secrets.token_hex(5)}@example.test", password_hash="x"),
        ]
        session.add_all(admins)
        session.flush()
        merchants = [Merchant(name=f"FK merchant {i} {secrets.token_hex(3)}", admin_user=admins[i]) for i in range(2)]
        session.add_all(merchants)
        session.flush()
        category = Category(merchant_id=merchants[1].id, name="other")
        session.add(category)
        session.flush()
        wrong_merchant_id, wrong_category_id = merchants[0].id, category.id
    with pytest.raises(IntegrityError) as error:
        with factory.begin() as session:
            session.execute(text(
                "INSERT INTO spus (merchant_id, category_id, name) VALUES (:merchant_id, :category_id, 'cross')"
            ), {"merchant_id": wrong_merchant_id, "category_id": wrong_category_id})
    assert error.value.orig.args[0] == 1452


def test_catalog_spu_and_sku_flush_failures_roll_back_without_audit(auth_client, monkeypatch):
    client, factory = auth_client
    admin_id, headers = _account(client, "flush-admin")
    merchant_id = _merchant(factory, admin_id, "flush")
    category = client.post("/api/v1/catalog/categories", headers=headers, json={"name": "Rollback"}).json()
    spu = client.post(
        "/api/v1/catalog/spus", headers=headers, json={"category_id": category["id"], "name": "Original"}
    ).json()
    sku = client.post(
        f"/api/v1/catalog/spus/{spu['id']}/skus", headers=headers,
        json={"sku_code": "ROLLBACK", "specification": "manual", "price": "3.00"},
    ).json()

    for service_call, assertion in (
        (lambda session: CatalogService.update_spu(
            session, actor_user_id=admin_id, spu_id=spu["id"], changes={"name": "Must rollback"}
        ), lambda session: session.get(Spu, spu["id"]).name == "Original"),
        (lambda session: CatalogService.update_sku(
            session, actor_user_id=admin_id, sku_id=sku["id"], changes={"price": Decimal("9.00")}
        ), lambda session: session.get(Sku, sku["id"]).price == Decimal("3.00")),
    ):
        with factory() as session:
            original_flush = session.flush

            def flush_then_fail(*args, **kwargs):
                original_flush(*args, **kwargs)
                raise RuntimeError("injected post-flush failure")

            monkeypatch.setattr(session, "flush", flush_then_fail)
            with pytest.raises(RuntimeError, match="injected post-flush failure"):
                service_call(session)
        with factory() as verify:
            assert assertion(verify)
            assert verify.scalar(select(func.count(AuditEvent.id)).where(
                AuditEvent.target_type == "spu", AuditEvent.target_id == spu["id"],
                AuditEvent.event_type == "catalog_spu_updated",
            )) == 0
            assert verify.scalar(select(func.count(AuditEvent.id)).where(
                AuditEvent.target_type == "sku", AuditEvent.target_id == sku["id"],
                AuditEvent.event_type == "catalog_sku_updated",
            )) == 0
    assert merchant_id > 0
