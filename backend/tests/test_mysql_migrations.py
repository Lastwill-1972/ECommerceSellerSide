import pytest
from alembic import command
from alembic.config import Config
from decimal import Decimal

from sqlalchemy import inspect, text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import Session

from app.core.config import BACKEND_DIR
from app.modules.merchants.models import Merchant
from app.modules.catalog.models import ProductOffshelfApplication
from app.modules.employees.models import EmployeeApplication, ShopEmployee, ShopEmployeeRole
from app.modules.shops.models import Shop, ShopApplication
from app.modules.users.models import User


def _stage_rows(connection, table):
    statement = (
        "SELECT id, merchant_id, name, owner_user_id, created_at, updated_at FROM shops ORDER BY id"
        if table == "shops" else f"SELECT * FROM `{table}` ORDER BY id"
    )
    return connection.execute(text(statement)).all()


def test_mysql_migration_constraints_rollback_and_round_trip(mysql_test_database):
    engine, _settings, _version, _database_name = mysql_test_database
    config = Config(str(BACKEND_DIR / "alembic.ini"))

    # Exercise the stage-six data backfill against a shop that predates this revision.
    command.downgrade(config, "789fe3dda218")
    with engine.begin() as connection:
        legacy_admin_id = connection.execute(
            text("INSERT INTO users (username, email, password_hash) VALUES ('legacy-admin', 'legacy-admin@example.test', 'x')")
        ).lastrowid
        legacy_owner_id = connection.execute(
            text("INSERT INTO users (username, email, password_hash) VALUES ('legacy-owner', 'legacy-owner@example.test', 'x')")
        ).lastrowid
        legacy_merchant_id = connection.execute(
            text("INSERT INTO merchants (name, admin_user_id) VALUES ('Legacy merchant', :admin)"),
            {"admin": legacy_admin_id},
        ).lastrowid
        connection.execute(
            text("INSERT INTO warehouses (merchant_id, name) VALUES (:merchant, 'Legacy warehouse')"),
            {"merchant": legacy_merchant_id},
        )
        legacy_shop_id = connection.execute(
            text("INSERT INTO shops (merchant_id, name, owner_user_id) VALUES (:merchant, 'Legacy shop', :owner)"),
            {"merchant": legacy_merchant_id, "owner": legacy_owner_id},
        ).lastrowid
    command.upgrade(config, "head")
    command.check(config)
    assert set(inspect(engine).get_table_names()) == {
        "alembic_version", "users", "merchants", "shops",
        "merchant_applications", "warehouses", "audit_events", "shop_applications",
        "shop_employee_roles", "shop_employees", "employee_applications",
        "categories", "spus", "skus", "product_offshelf_applications",
        "warehouse_access_applications", "warehouse_accesses", "stocks", "stock_movements", "orders", "order_items",
    }
    assert {item["name"] for item in inspect(engine).get_check_constraints("spus")} == {"ck_spus_status"}
    assert {item["name"] for item in inspect(engine).get_check_constraints("skus")} == {
        "ck_skus_price_nonnegative"
    }
    assert {item["name"] for item in inspect(engine).get_check_constraints("product_offshelf_applications")} == {
        "ck_product_offshelf_applications_status"
    }
    assert {item["name"] for item in inspect(engine).get_unique_constraints("product_offshelf_applications")} == {
        "uq_product_offshelf_applications_pending_spu"
    }
    assert {
        item["name"] for item in inspect(engine).get_indexes("product_offshelf_applications")
    } >= {
        "ix_product_offshelf_applications_spu_id",
        "ix_product_offshelf_applications_applicant_user_id",
        "ix_product_offshelf_applications_status_id",
    }
    assert all(
        item["options"].get("ondelete") == "RESTRICT"
        for item in inspect(engine).get_foreign_keys("product_offshelf_applications")
    )
    assert {item["name"] for item in inspect(engine).get_check_constraints("warehouse_access_applications")} == {
        "ck_warehouse_access_applications_status"
    }
    assert {item["name"] for item in inspect(engine).get_unique_constraints("warehouse_access_applications")} == {
        "uq_warehouse_access_applications_pending_shop"
    }
    assert {item["name"] for item in inspect(engine).get_unique_constraints("warehouse_accesses")} == {
        "uq_warehouse_accesses_shop_id"
    }
    assert {item["name"] for item in inspect(engine).get_unique_constraints("shops")} >= {"uq_shops_merchant_id"}
    assert {item["name"] for item in inspect(engine).get_unique_constraints("warehouses")} >= {
        "uq_warehouses_merchant_id_id"
    }
    assert {tuple(item["constrained_columns"]) for item in inspect(engine).get_foreign_keys("warehouse_accesses")} >= {
        ("merchant_id", "shop_id"), ("merchant_id", "warehouse_id"), ("granted_by_user_id",)
    }
    assert all(item["options"].get("ondelete") == "RESTRICT"
               for item in inspect(engine).get_foreign_keys("warehouse_accesses"))
    with engine.connect() as connection:
        generated = connection.execute(text(
            "SELECT EXTRA, GENERATION_EXPRESSION FROM information_schema.COLUMNS "
            "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'product_offshelf_applications' "
            "AND COLUMN_NAME = 'pending_spu_id'"
        )).one()
        assert "STORED GENERATED" in generated.EXTRA.upper()
        assert "status" in generated.GENERATION_EXPRESSION and "spu_id" in generated.GENERATION_EXPRESSION
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == "20261017_phase12"
        assert connection.scalar(text(
            "SELECT COUNT(*) FROM shop_employee_roles WHERE shop_id = :shop AND name = '普通员工'"
        ), {"shop": legacy_shop_id}) == 1
    with engine.begin() as connection:
        legacy_category_id = connection.execute(
            text("INSERT INTO categories (merchant_id, name) VALUES (:merchant, 'Legacy category')"),
            {"merchant": legacy_merchant_id},
        ).lastrowid
        legacy_spu_id = connection.execute(
            text("INSERT INTO spus (merchant_id, category_id, name) VALUES (:merchant, :category, 'Legacy SPU')"),
            {"merchant": legacy_merchant_id, "category": legacy_category_id},
        ).lastrowid
    # Put a historical SKU into the phase-nine schema and re-run phase ten to
    # verify tenant-key and zero-stock backfill against an existing product.
    command.downgrade(config, "20261017_warehouse_access")
    with engine.begin() as connection:
        legacy_sku_id = connection.execute(
            text(
                "INSERT INTO skus (spu_id, sku_code, specification, price) "
                "VALUES (:spu, 'LEGACY-1', 'manual', 12.30)"
            ),
            {"spu": legacy_spu_id},
        ).lastrowid
    command.upgrade(config, "head")
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT quantity FROM stocks WHERE sku_id = :id"), {"id": legacy_sku_id}) == 0
        assert connection.scalar(text("SELECT merchant_id FROM skus WHERE id = :id"), {"id": legacy_sku_id}) == legacy_merchant_id
    with engine.begin() as connection:
        second_spu_id = connection.execute(
            text("INSERT INTO spus (merchant_id, category_id, name) VALUES (:merchant, :category, 'Second Legacy SPU')"),
            {"merchant": legacy_merchant_id, "category": legacy_category_id},
        ).lastrowid
        second_sku_id = connection.execute(
            text(
                "INSERT INTO skus (merchant_id, spu_id, sku_code, specification, price) "
                "VALUES (:merchant, :spu, 'LEGACY-1', 'same code on other SPU', 0.00)"
            ),
            {"merchant": legacy_merchant_id, "spu": second_spu_id},
        ).lastrowid
        connection.execute(
            text("INSERT INTO stocks (merchant_id, warehouse_id, sku_id, quantity) VALUES (:merchant, :warehouse, :sku, 0)"),
            {"merchant": legacy_merchant_id, "warehouse": connection.scalar(
                text("SELECT id FROM warehouses WHERE merchant_id = :merchant"), {"merchant": legacy_merchant_id}
            ), "sku": second_sku_id},
        )
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT status FROM spus WHERE id = :id"), {"id": legacy_spu_id}) == "OFF_SALE"
        assert connection.scalar(text("SELECT price FROM skus WHERE id = :id"), {"id": legacy_sku_id}) == Decimal("12.30")
    with pytest.raises(IntegrityError) as error:
        with engine.begin() as connection:
            connection.execute(text(
                "INSERT INTO skus (merchant_id, spu_id, sku_code, specification, price) "
                "VALUES (:merchant, :spu, 'LEGACY-1', 'duplicate in same SPU', 1.00)"
            ), {"merchant": legacy_merchant_id, "spu": legacy_spu_id})
    assert error.value.orig.args[0] == 1062

    def insert_user(connection, username, email):
        result = connection.execute(
            text(
                "INSERT INTO users (username, email, password_hash) "
                "VALUES (:username, :email, 'test-hash')"
            ),
            {"username": username, "email": email},
        )
        return result.lastrowid

    def assert_rejected(statement, parameters, error_code):
        with pytest.raises(IntegrityError) as error:
            with engine.begin() as connection:
                connection.execute(text(statement), parameters)
        assert error.value.orig.args[0] == error_code

    with engine.begin() as connection:
        admin_id = insert_user(connection, "platform-admin", "admin@example.test")
        owner_id = insert_user(connection, "shop-owner", "owner@example.test")
        second_owner_id = insert_user(connection, "second-owner", "second@example.test")
        other_admin_id = insert_user(connection, "other-admin", "other@example.test")
        name_collision_owner_id = insert_user(connection, "name-owner", "name-owner@example.test")
        missing_merchant_owner_id = insert_user(connection, "merchant-fk-owner", "merchant-fk@example.test")
        missing_owner_merchant_admin_id = insert_user(connection, "shop-fk-admin", "shop-fk-admin@example.test")

    assert_rejected(
        "INSERT INTO users (username, email, password_hash) VALUES ('PLATFORM-ADMIN', 'new@example.test', 'x')",
        {},
        1062,
    )
    assert_rejected(
        "INSERT INTO users (username, email, password_hash) VALUES ('new-user', 'ADMIN@example.test', 'x')",
        {},
        1062,
    )

    with engine.begin() as connection:
        merchant_id = connection.execute(
            text("INSERT INTO merchants (name, admin_user_id) VALUES ('Merchant One', :user_id)"),
            {"user_id": admin_id},
        ).lastrowid
        other_merchant_id = connection.execute(
            text("INSERT INTO merchants (name, admin_user_id) VALUES ('Merchant Two', :user_id)"),
            {"user_id": other_admin_id},
        ).lastrowid

    with engine.begin() as connection:
        connection.execute(
            text("INSERT INTO warehouses (merchant_id, name) VALUES (:merchant, 'Default warehouse')"),
            {"merchant": merchant_id},
        )

    assert_rejected(
        "INSERT INTO warehouses (merchant_id, name) VALUES (:merchant, 'Second warehouse')",
        {"merchant": merchant_id},
        1062,
    )
    with pytest.raises(DBAPIError) as error:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO merchant_applications (applicant_user_id, merchant_name, status) "
                    "VALUES (:applicant, 'Invalid status', 'pending')"
                ),
                {"applicant": owner_id},
            )
    assert error.value.orig.args[0] == 3819
    with pytest.raises(DBAPIError) as error:
        with engine.begin() as connection:
            connection.execute(
                text("INSERT INTO spus (merchant_id, category_id, name, status) VALUES (:merchant, :category, 'bad', 'on_sale')"),
                {"merchant": legacy_merchant_id, "category": legacy_category_id},
            )
    assert error.value.orig.args[0] == 3819
    with pytest.raises(DBAPIError) as error:
        with engine.begin() as connection:
            connection.execute(
                text("INSERT INTO skus (merchant_id, spu_id, sku_code, specification, price) VALUES (:merchant, :spu, 'NEGATIVE', 'manual', -0.01)"),
                {"merchant": legacy_merchant_id, "spu": legacy_spu_id},
            )
    assert error.value.orig.args[0] == 3819

    assert_rejected(
        "INSERT INTO merchants (name, admin_user_id) VALUES ('Duplicate admin', :user_id)",
        {"user_id": admin_id},
        1062,
    )
    assert_rejected(
        "INSERT INTO merchants (name, admin_user_id) VALUES ('Missing user', 9223372036854775000)",
        {},
        1452,
    )

    with engine.begin() as connection:
        shop_id = connection.execute(
            text("INSERT INTO shops (merchant_id, name, owner_user_id) VALUES (:merchant, 'Main Shop', :owner)"),
            {"merchant": merchant_id, "owner": owner_id},
        ).lastrowid
        # A shop name can be reused under another merchant.
        connection.execute(
            text("INSERT INTO shops (merchant_id, name, owner_user_id) VALUES (:merchant, 'Main Shop', :owner)"),
            {"merchant": other_merchant_id, "owner": second_owner_id},
        )
        connection.execute(
            text("INSERT INTO shop_employee_roles (shop_id, name) VALUES (:shop, '普通员工')"),
            {"shop": shop_id},
        )
        connection.execute(
            text(
                "INSERT INTO shop_employee_roles (shop_id, name) "
                "SELECT id, '普通员工' FROM shops WHERE merchant_id = :merchant AND owner_user_id = :owner"
            ),
            {"merchant": other_merchant_id, "owner": second_owner_id},
        )
        default_role_id = connection.scalar(text(
            "SELECT id FROM shop_employee_roles WHERE shop_id = :shop AND name = '普通员工'"
        ), {"shop": shop_id})
        other_shop_id = connection.scalar(text(
            "SELECT id FROM shops WHERE merchant_id = :merchant AND owner_user_id = :owner"
        ), {"merchant": other_merchant_id, "owner": second_owner_id})
        other_role_id = connection.scalar(text(
            "SELECT id FROM shop_employee_roles WHERE shop_id = :shop AND name = '普通员工'"
        ), {"shop": other_shop_id})
    assert shop_id
    assert default_role_id is not None and other_role_id is not None

    # A user has one employee identity globally; role/shop consistency is enforced by a composite FK.
    with engine.begin() as connection:
        employee_id = connection.execute(
            text("INSERT INTO shop_employees (shop_id, user_id, role_id) VALUES (:shop, :user, :role)"),
            {"shop": shop_id, "user": name_collision_owner_id, "role": default_role_id},
        ).lastrowid
    assert employee_id
    assert_rejected(
        "INSERT INTO shop_employees (shop_id, user_id, role_id) VALUES (:shop, :user, :role)",
        {"shop": other_shop_id, "user": missing_merchant_owner_id, "role": default_role_id},
        1452,
    )
    assert_rejected(
        "INSERT INTO shop_employees (shop_id, user_id, role_id) VALUES (:shop, :user, :role)",
        {"shop": other_shop_id, "user": name_collision_owner_id, "role": other_role_id},
        1062,
    )
    with pytest.raises(DBAPIError) as error:
        with engine.begin() as connection:
            connection.execute(
                text("INSERT INTO employee_applications (shop_id, applicant_user_id, status) VALUES (:shop, :user, 'pending')"),
                {"shop": shop_id, "user": missing_owner_merchant_admin_id},
            )
    assert error.value.orig.args[0] == 3819

    with engine.begin() as connection:
        shop_application_id = connection.execute(
            text(
                "INSERT INTO shop_applications (merchant_id, applicant_user_id, shop_name) "
                "VALUES (:merchant, :applicant, 'Pending shop')"
            ),
            {"merchant": other_merchant_id, "applicant": name_collision_owner_id},
        ).lastrowid
    assert shop_application_id
    with pytest.raises(DBAPIError) as error:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO shop_applications (merchant_id, applicant_user_id, shop_name, status) "
                    "VALUES (:merchant, :applicant, 'Bad status', 'pending')"
                ),
                {"merchant": other_merchant_id, "applicant": name_collision_owner_id},
            )
    assert error.value.orig.args[0] == 3819
    assert_rejected(
        "INSERT INTO shop_applications (merchant_id, applicant_user_id, shop_name) "
        "VALUES (9223372036854775000, :applicant, 'Missing merchant')",
        {"applicant": name_collision_owner_id},
        1452,
    )

    assert_rejected(
        "INSERT INTO shops (merchant_id, name, owner_user_id) VALUES (:merchant, 'main shop', :owner)",
        {"merchant": merchant_id, "owner": name_collision_owner_id},
        1062,
    )
    assert_rejected(
        "INSERT INTO shops (merchant_id, name, owner_user_id) VALUES (:merchant, 'Another Shop', :owner)",
        {"merchant": merchant_id, "owner": owner_id},
        1062,
    )
    assert_rejected(
        "INSERT INTO shops (merchant_id, name, owner_user_id) VALUES (9223372036854775000, 'Missing merchant', :owner)",
        {"owner": missing_merchant_owner_id},
        1452,
    )
    with engine.begin() as connection:
        missing_owner_merchant_id = connection.execute(
            text("INSERT INTO merchants (name, admin_user_id) VALUES ('Shop FK merchant', :admin)"),
            {"admin": missing_owner_merchant_admin_id},
        ).lastrowid
    assert_rejected(
        "INSERT INTO shops (merchant_id, name, owner_user_id) VALUES (:merchant, 'Missing owner', 9223372036854775000)",
        {"merchant": missing_owner_merchant_id},
        1452,
    )

    with Session(engine) as session, session.begin():
        admin = User(username="orm-admin", email="orm-admin@example.test", password_hash="test-hash")
        owner = User(username="orm-owner", email="orm-owner@example.test", password_hash="test-hash")
        merchant = Merchant(name="ORM Merchant", admin_user=admin)
        shop = Shop(name="ORM Shop", merchant=merchant, owner_user=owner)
        session.add_all([admin, owner, merchant, shop])
        session.flush()
        assert merchant.admin_user is admin
        assert shop.merchant is merchant
        assert shop.owner_user is owner
        session.refresh(admin)
        assert admin.is_platform_admin is False
        assert admin.is_active is True
        assert admin.created_at is not None and admin.updated_at is not None

    with pytest.raises(RuntimeError, match="rollback probe"):
        with engine.begin() as connection:
            connection.execute(
                text("INSERT INTO users (username, email, password_hash) VALUES ('rolled-back', 'rollback@example.test', 'x')")
            )
            raise RuntimeError("rollback probe")
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT COUNT(*) FROM users WHERE username = 'rolled-back'")) == 0

    with engine.begin() as connection:
        connection.execute(text("UPDATE spus SET status = 'ON_SALE' WHERE id = :id"), {"id": legacy_spu_id})
        first_application_id = connection.execute(
            text("INSERT INTO product_offshelf_applications (spu_id, applicant_user_id) VALUES (:spu, :user)"),
            {"spu": legacy_spu_id, "user": legacy_admin_id},
        ).lastrowid
    with pytest.raises(DBAPIError) as lowercase_status_error:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO product_offshelf_applications (spu_id, applicant_user_id, status) "
                    "VALUES (:spu, :user, 'pending')"
                ),
                {"spu": legacy_spu_id, "user": legacy_admin_id},
            )
    assert lowercase_status_error.value.orig.args[0] == 3819
    with pytest.raises(IntegrityError) as pending_error:
        with engine.begin() as connection:
            connection.execute(
                text("INSERT INTO product_offshelf_applications (spu_id, applicant_user_id) VALUES (:spu, :user)"),
                {"spu": legacy_spu_id, "user": legacy_admin_id},
            )
    assert pending_error.value.orig.args[0] == 1062
    with engine.begin() as connection:
        connection.execute(
            text("UPDATE product_offshelf_applications SET status = 'REJECTED' WHERE id = :id"),
            {"id": first_application_id},
        )
        connection.execute(
            text("INSERT INTO product_offshelf_applications (spu_id, applicant_user_id) VALUES (:spu, :user)"),
            {"spu": legacy_spu_id, "user": legacy_admin_id},
        )

    # A SKU whose merchant has no warehouse must stop phase ten before any DDL.
    command.downgrade(config, "20261017_warehouse_access")
    with engine.begin() as connection:
        missing_admin_id = connection.execute(text(
            "INSERT INTO users (username, email, password_hash) VALUES ('no-warehouse-admin', 'no-warehouse@example.test', 'x')"
        )).lastrowid
        missing_merchant_id = connection.execute(text(
            "INSERT INTO merchants (name, admin_user_id) VALUES ('No warehouse merchant', :admin)"
        ), {"admin": missing_admin_id}).lastrowid
        missing_category_id = connection.execute(text(
            "INSERT INTO categories (merchant_id, name) VALUES (:merchant, 'No warehouse category')"
        ), {"merchant": missing_merchant_id}).lastrowid
        missing_spu_id = connection.execute(text(
            "INSERT INTO spus (merchant_id, category_id, name) VALUES (:merchant, :category, 'No warehouse SPU')"
        ), {"merchant": missing_merchant_id, "category": missing_category_id}).lastrowid
        missing_sku_id = connection.execute(text(
            "INSERT INTO skus (spu_id, sku_code, specification, price) VALUES (:spu, 'NO-WH', 'manual', 1.00)"
        ), {"spu": missing_spu_id}).lastrowid
    with pytest.raises(RuntimeError, match="historical SKU.*no warehouse"):
        command.upgrade(config, "head")
    assert "stocks" not in inspect(engine).get_table_names()
    assert "merchant_id" not in {column["name"] for column in inspect(engine).get_columns("skus")}
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == "20261017_warehouse_access"
    with engine.begin() as connection:
        connection.execute(text(
            "INSERT INTO warehouses (merchant_id, name) VALUES (:merchant, 'Repaired warehouse')"
        ), {"merchant": missing_merchant_id})
    command.upgrade(config, "head")
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT quantity FROM stocks WHERE sku_id = :sku"), {"sku": missing_sku_id}) == 0

    with engine.connect() as connection:
        stage7_tables = (
            "users", "merchants", "shops", "merchant_applications", "warehouses", "audit_events",
            "shop_applications", "shop_employee_roles", "shop_employees", "employee_applications",
            "categories", "spus",
        )
        stage7_rows_before = {
            table: _stage_rows(connection, table)
            for table in stage7_tables
        }
        sku_values_before = connection.execute(
            text("SELECT id, spu_id, sku_code, specification, price, created_at, updated_at FROM skus ORDER BY id")
        ).all()
        stage6_tables = (
            "users", "merchants", "shops", "merchant_applications", "warehouses", "audit_events",
            "shop_applications", "shop_employee_roles", "shop_employees", "employee_applications",
        )
        legacy_rows_before = {
            table: _stage_rows(connection, table)
            for table in stage6_tables
        }
        user_count_before_incremental_down = connection.scalar(text("SELECT COUNT(*) FROM users"))
        shop_count_before_incremental_down = connection.scalar(text("SELECT COUNT(*) FROM shops"))
        legacy_values_before = connection.execute(
            text(
                "SELECT u.username, u.email, m.name, m.admin_user_id, s.merchant_id, s.name, s.owner_user_id "
                "FROM users AS u JOIN merchants AS m ON m.admin_user_id = :admin "
                "JOIN shops AS s ON s.id = :shop WHERE u.id = :admin"
            ),
            {"admin": legacy_admin_id, "shop": legacy_shop_id},
        ).one()
    # Exercise the phase-nine downgrade separately so parent constraints disappear
    # while the older shop/warehouse foreign-key indexes remain usable.
    command.downgrade(config, "20261017_offshelf")
    phase8_tables = set(inspect(engine).get_table_names())
    assert not ({"warehouse_access_applications", "warehouse_accesses"} & phase8_tables)
    assert {item["name"] for item in inspect(engine).get_unique_constraints("shops")} == {
        "uq_shops_merchant_name", "uq_shops_owner_user_id",
    }
    warehouse_unique_names = {item["name"] for item in inspect(engine).get_unique_constraints("warehouses")}
    assert "uq_warehouses_merchant_id" in warehouse_unique_names
    assert "uq_warehouses_merchant_id_id" not in warehouse_unique_names
    assert any(item["constrained_columns"] == ["merchant_id"] for item in inspect(engine).get_foreign_keys("warehouses"))
    command.upgrade(config, "head")
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == "20261017_phase12"
        for table, rows in stage7_rows_before.items():
            assert _stage_rows(connection, table) == rows
        assert connection.execute(text(
            "SELECT id, spu_id, sku_code, specification, price, created_at, updated_at FROM skus ORDER BY id"
        )).all() == sku_values_before
        assert connection.scalar(text("SELECT COUNT(*) FROM stocks")) == connection.scalar(text("SELECT COUNT(*) FROM skus"))
        assert connection.scalar(text("SELECT COUNT(*) FROM stocks WHERE quantity <> 0")) == 0

    # MySQL DDL is not transactional; exercise the full phase-eight down/up independently.
    command.downgrade(config, "20261017_catalog")
    assert not ({"product_offshelf_applications", "warehouse_access_applications", "warehouse_accesses"} & set(inspect(engine).get_table_names()))
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == "20261017_catalog"
        for table, rows in stage7_rows_before.items():
            assert _stage_rows(connection, table) == rows
    command.upgrade(config, "head")
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == "20261017_phase12"
        for table, rows in stage7_rows_before.items():
            assert _stage_rows(connection, table) == rows
    command.downgrade(config, "a6e73c92f014")
    assert not ({"categories", "spus", "skus", "product_offshelf_applications", "warehouse_access_applications", "warehouse_accesses"} & set(inspect(engine).get_table_names()))
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == "a6e73c92f014"
        for table, rows in legacy_rows_before.items():
            assert _stage_rows(connection, table) == rows
    command.upgrade(config, "head")
    assert {"categories", "spus", "skus", "product_offshelf_applications", "warehouse_access_applications", "warehouse_accesses", "stocks", "stock_movements", "orders", "order_items"} <= set(inspect(engine).get_table_names())
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == "20261017_phase12"
        for table, rows in legacy_rows_before.items():
            assert _stage_rows(connection, table) == rows
    command.downgrade(config, "204e6bcc8f59")
    remaining_tables = set(inspect(engine).get_table_names())
    assert "shop_applications" not in remaining_tables
    assert not ({"shop_employee_roles", "shop_employees", "employee_applications"} & remaining_tables)
    assert not ({"categories", "spus", "skus", "product_offshelf_applications", "warehouse_access_applications", "warehouse_accesses"} & remaining_tables)
    assert {"users", "merchants", "shops", "merchant_applications", "warehouses", "audit_events"} <= remaining_tables
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == "204e6bcc8f59"
        assert connection.scalar(text("SELECT COUNT(*) FROM users")) == user_count_before_incremental_down
        assert connection.scalar(text("SELECT COUNT(*) FROM shops")) == shop_count_before_incremental_down
        legacy_values_after = connection.execute(
            text(
                "SELECT u.username, u.email, m.name, m.admin_user_id, s.merchant_id, s.name, s.owner_user_id "
                "FROM users AS u JOIN merchants AS m ON m.admin_user_id = :admin "
                "JOIN shops AS s ON s.id = :shop WHERE u.id = :admin"
            ),
            {"admin": legacy_admin_id, "shop": legacy_shop_id},
        ).one()
        assert legacy_values_after == legacy_values_before
    command.upgrade(config, "head")
    assert "shop_applications" in inspect(engine).get_table_names()
    assert {"shop_employee_roles", "shop_employees", "employee_applications"} <= set(inspect(engine).get_table_names())
    command.downgrade(config, "base")
    assert not ({"users", "merchants", "shops", "merchant_applications", "warehouses", "audit_events", "shop_applications"} & set(inspect(engine).get_table_names()))
    command.upgrade(config, "head")
    assert {"users", "merchants", "shops", "merchant_applications", "warehouses", "audit_events", "shop_applications", "shop_employee_roles", "shop_employees", "employee_applications", "categories", "spus", "skus", "product_offshelf_applications", "warehouse_access_applications", "warehouse_accesses", "stocks", "stock_movements", "orders", "order_items"} <= set(inspect(engine).get_table_names())
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == "20261017_phase12"
    with engine.begin() as connection:
        for statement in (
            "DELETE FROM audit_events", "DELETE FROM stock_movements", "DELETE FROM order_items", "DELETE FROM orders", "DELETE FROM stocks", "DELETE FROM warehouse_accesses", "DELETE FROM warehouse_access_applications", "DELETE FROM product_offshelf_applications", "DELETE FROM skus", "DELETE FROM spus", "DELETE FROM categories",
            "DELETE FROM employee_applications", "DELETE FROM shop_employees", "DELETE FROM shop_employee_roles",
            "DELETE FROM shop_applications", "DELETE FROM merchant_applications", "DELETE FROM warehouses",
            "DELETE FROM shops", "DELETE FROM merchants", "DELETE FROM users",
        ):
            connection.execute(text(statement))
