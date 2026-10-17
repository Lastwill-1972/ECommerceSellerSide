import pytest
import os
import secrets
from unittest.mock import patch
from fastapi.testclient import TestClient

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text

from app.core.config import BACKEND_DIR, Settings, get_settings


@pytest.fixture
def auth_client(mysql_test_session_factory, monkeypatch):
    monkeypatch.setenv("AUTH_JWT_SECRET", secrets.token_urlsafe(48))
    monkeypatch.setenv("AUTH_JWT_ACCESS_TOKEN_EXPIRE_MINUTES", "30")
    get_settings.cache_clear()
    from app.main import create_app

    with TestClient(create_app()) as client:
        yield client, mysql_test_session_factory
    get_settings.cache_clear()


@pytest.fixture(autouse=True)
def isolated_settings(monkeypatch):
    # Never read developer credentials or connect to their database during tests.
    from app.core import config

    monkeypatch.setattr(config.Settings, "model_config", {**config.Settings.model_config, "env_file": None})
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("DEBUG", "false")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture(scope="session")
def mysql_test_database():
    """Reuse only the explicitly approved dedicated test DB; never touch dev databases."""
    test_env_file = BACKEND_DIR / ".env.test"
    settings = Settings(_env_file=test_env_file if test_env_file.is_file() else None)
    database_name = os.environ.get("TEST_DB_NAME") or settings.db_name
    approved = os.environ.get("MYSQL_TEST_DB_APPROVED")
    if test_env_file.is_file() and approved is None:
        from dotenv import dotenv_values

        approved = dotenv_values(test_env_file).get("MYSQL_TEST_DB_APPROVED")
    required = {
        "DB_HOST": settings.db_host,
        "DB_USER": settings.db_user,
        "DB_PASSWORD": settings.db_password,
        "TEST_DB_NAME or DB_NAME in backend/.env.test": database_name,
    }
    missing = [key for key, value in required.items() if not value]
    if missing:
        pytest.skip(f"MySQL integration configuration missing: {', '.join(missing)}")
    if approved != "1":
        pytest.skip("set MYSQL_TEST_DB_APPROVED=1 only for a dedicated disposable test database")
    if not (database_name.lower().startswith("test_") or database_name.lower().endswith("_test")):
        pytest.skip("test database name must begin with test_ or end with _test")

    settings = settings.model_copy(update={"db_name": database_name, "db_connect_timeout": 5})
    engine = create_engine(
        settings.database_url,
        pool_pre_ping=True,
        connect_args={"connect_timeout": settings.db_connect_timeout},
    )
    safe_to_cleanup = False
    session_caches_ready = False
    session_settings_patch = patch("app.db.session.get_settings", return_value=settings)
    session_settings_patch.start()
    try:
        with engine.connect() as connection:
            server_version, actual_database = connection.execute(text("SELECT VERSION(), DATABASE()")).one()
            if actual_database != database_name:
                pytest.skip("connected database name does not match TEST_DB_NAME")
            tables = set(inspect(connection).get_table_names())
            views = inspect(connection).get_view_names()
            if views:
                pytest.skip("refusing to use a test database containing views")

        expected_old = {"alembic_version", "users", "merchants", "shops"}
        expected_stage4 = {
            "alembic_version", "users", "merchants", "shops",
            "merchant_applications", "warehouses", "audit_events",
        }
        expected_stage5 = expected_stage4 | {"shop_applications"}
        expected = {
            "alembic_version", "users", "merchants", "shops",
            "merchant_applications", "warehouses", "audit_events", "shop_applications",
            "shop_employee_roles", "shop_employees", "employee_applications",
        }
        expected_catalog = expected | {"categories", "spus", "skus"}
        expected_offshelf = expected_catalog | {"product_offshelf_applications"}
        expected_warehouse_access = expected_offshelf | {"warehouse_access_applications", "warehouse_accesses"}
        expected_inventory = expected_warehouse_access | {"stocks", "stock_movements"}
        expected_orders = expected_inventory | {"orders", "order_items"}
        expected_phase12 = expected_orders
        revision = None
        if tables:
            if tables not in (expected_old, expected_stage4, expected_stage5, expected, expected_catalog, expected_offshelf, expected_warehouse_access, expected_inventory, expected_orders):
                pytest.skip("refusing a non-empty database without the expected identity schema")
            with engine.connect() as connection:
                revision = connection.scalar(text("SELECT version_num FROM alembic_version"))
            if (tables == expected_old and revision != "2ba874a754ef") or (
                tables == expected_stage4 and revision != "204e6bcc8f59"
            ) or (
                tables == expected_stage5 and revision != "789fe3dda218"
            ) or (
                tables == expected and revision != "a6e73c92f014"
            ) or (
                tables == expected_catalog and revision != "20261017_catalog"
            ) or (
                tables == expected_offshelf and revision != "20261017_offshelf"
            ) or (
                tables == expected_warehouse_access and revision != "20261017_warehouse_access"
            ) or (
                tables == expected_inventory and revision != "20261017_inventory"
            ) or (
                tables == expected_orders and revision not in ("20261017_orders", "20261017_phase12")
            ):
                pytest.skip("test database is not at the expected migration head")
            with engine.connect() as connection:
                if tables == expected_old:
                    row_counts = connection.execute(
                        text("SELECT (SELECT COUNT(*) FROM users), (SELECT COUNT(*) FROM merchants), (SELECT COUNT(*) FROM shops)")
                    ).one()
                elif tables == expected_stage4:
                    row_counts = connection.execute(
                        text(
                            "SELECT (SELECT COUNT(*) FROM users), (SELECT COUNT(*) FROM merchants), "
                            "(SELECT COUNT(*) FROM shops), (SELECT COUNT(*) FROM merchant_applications), "
                            "(SELECT COUNT(*) FROM warehouses), (SELECT COUNT(*) FROM audit_events)"
                        )
                    ).one()
                elif tables == expected_stage5:
                    row_counts = connection.execute(
                        text(
                            "SELECT (SELECT COUNT(*) FROM users), (SELECT COUNT(*) FROM merchants), "
                            "(SELECT COUNT(*) FROM shops), (SELECT COUNT(*) FROM merchant_applications), "
                            "(SELECT COUNT(*) FROM warehouses), (SELECT COUNT(*) FROM audit_events), "
                            "(SELECT COUNT(*) FROM shop_applications)"
                        )
                    ).one()
                elif tables == expected:
                    row_counts = connection.execute(
                        text(
                            "SELECT (SELECT COUNT(*) FROM users), (SELECT COUNT(*) FROM merchants), "
                            "(SELECT COUNT(*) FROM shops), (SELECT COUNT(*) FROM merchant_applications), "
                            "(SELECT COUNT(*) FROM warehouses), (SELECT COUNT(*) FROM audit_events), "
                            "(SELECT COUNT(*) FROM shop_applications), "
                            "(SELECT COUNT(*) FROM shop_employee_roles), "
                            "(SELECT COUNT(*) FROM shop_employees), "
                            "(SELECT COUNT(*) FROM employee_applications)"
                        )
                    ).one()
                elif tables == expected_catalog:
                    row_counts = connection.execute(
                        text(
                            "SELECT (SELECT COUNT(*) FROM users), (SELECT COUNT(*) FROM merchants), "
                            "(SELECT COUNT(*) FROM shops), (SELECT COUNT(*) FROM merchant_applications), "
                            "(SELECT COUNT(*) FROM warehouses), (SELECT COUNT(*) FROM audit_events), "
                            "(SELECT COUNT(*) FROM shop_applications), (SELECT COUNT(*) FROM shop_employee_roles), "
                            "(SELECT COUNT(*) FROM shop_employees), (SELECT COUNT(*) FROM employee_applications), "
                            "(SELECT COUNT(*) FROM categories), (SELECT COUNT(*) FROM spus), (SELECT COUNT(*) FROM skus)"
                        )
                    ).one()
                elif tables == expected_offshelf:
                    row_counts = connection.execute(
                        text(
                            "SELECT (SELECT COUNT(*) FROM users), (SELECT COUNT(*) FROM merchants), "
                            "(SELECT COUNT(*) FROM shops), (SELECT COUNT(*) FROM merchant_applications), "
                            "(SELECT COUNT(*) FROM warehouses), (SELECT COUNT(*) FROM audit_events), "
                            "(SELECT COUNT(*) FROM shop_applications), (SELECT COUNT(*) FROM shop_employee_roles), "
                            "(SELECT COUNT(*) FROM shop_employees), (SELECT COUNT(*) FROM employee_applications), "
                            "(SELECT COUNT(*) FROM categories), (SELECT COUNT(*) FROM spus), (SELECT COUNT(*) FROM skus), "
                            "(SELECT COUNT(*) FROM product_offshelf_applications)"
                        )
                    ).one()
                elif tables == expected_warehouse_access:
                    row_counts = connection.execute(
                        text(
                            "SELECT (SELECT COUNT(*) FROM users), (SELECT COUNT(*) FROM merchants), "
                            "(SELECT COUNT(*) FROM shops), (SELECT COUNT(*) FROM merchant_applications), "
                            "(SELECT COUNT(*) FROM warehouses), (SELECT COUNT(*) FROM audit_events), "
                            "(SELECT COUNT(*) FROM shop_applications), (SELECT COUNT(*) FROM shop_employee_roles), "
                            "(SELECT COUNT(*) FROM shop_employees), (SELECT COUNT(*) FROM employee_applications), "
                            "(SELECT COUNT(*) FROM categories), (SELECT COUNT(*) FROM spus), (SELECT COUNT(*) FROM skus), "
                            "(SELECT COUNT(*) FROM product_offshelf_applications), "
                            "(SELECT COUNT(*) FROM warehouse_access_applications), (SELECT COUNT(*) FROM warehouse_accesses)"
                        )
                    ).one()
                elif tables == expected_inventory:
                    row_counts = connection.execute(
                        text(
                            "SELECT (SELECT COUNT(*) FROM users), (SELECT COUNT(*) FROM merchants), "
                            "(SELECT COUNT(*) FROM shops), (SELECT COUNT(*) FROM merchant_applications), "
                            "(SELECT COUNT(*) FROM warehouses), (SELECT COUNT(*) FROM audit_events), "
                            "(SELECT COUNT(*) FROM shop_applications), (SELECT COUNT(*) FROM shop_employee_roles), "
                            "(SELECT COUNT(*) FROM shop_employees), (SELECT COUNT(*) FROM employee_applications), "
                            "(SELECT COUNT(*) FROM categories), (SELECT COUNT(*) FROM spus), (SELECT COUNT(*) FROM skus), "
                            "(SELECT COUNT(*) FROM product_offshelf_applications), "
                            "(SELECT COUNT(*) FROM warehouse_access_applications), (SELECT COUNT(*) FROM warehouse_accesses), "
                            "(SELECT COUNT(*) FROM stocks), (SELECT COUNT(*) FROM stock_movements)"
                        )
                    ).one()
                elif tables == expected_orders:
                    row_counts = connection.execute(
                        text(
                            "SELECT (SELECT COUNT(*) FROM users), (SELECT COUNT(*) FROM merchants), "
                            "(SELECT COUNT(*) FROM shops), (SELECT COUNT(*) FROM merchant_applications), "
                            "(SELECT COUNT(*) FROM warehouses), (SELECT COUNT(*) FROM audit_events), "
                            "(SELECT COUNT(*) FROM shop_applications), (SELECT COUNT(*) FROM shop_employee_roles), "
                            "(SELECT COUNT(*) FROM shop_employees), (SELECT COUNT(*) FROM employee_applications), "
                            "(SELECT COUNT(*) FROM categories), (SELECT COUNT(*) FROM spus), (SELECT COUNT(*) FROM skus), "
                            "(SELECT COUNT(*) FROM product_offshelf_applications), "
                            "(SELECT COUNT(*) FROM warehouse_access_applications), (SELECT COUNT(*) FROM warehouse_accesses), "
                            "(SELECT COUNT(*) FROM stocks), (SELECT COUNT(*) FROM stock_movements), "
                            "(SELECT COUNT(*) FROM orders), (SELECT COUNT(*) FROM order_items)"
                        )
                    ).one()
            if any(row_counts):
                pytest.skip("refusing a test database containing business rows")

        from app.db import session as db_session

        db_session.get_engine.cache_clear()
        db_session.get_session_factory.cache_clear()
        session_caches_ready = True
        if not tables or revision != "20261017_phase12":
            command.upgrade(Config(str(BACKEND_DIR / "alembic.ini")), "head")
        safe_to_cleanup = True

        # This database is dedicated to tests. Clear leftovers from interrupted runs using
        # FK order, while preserving the schema for all auth and migration checks.
        with engine.begin() as connection:
            shop_columns = {column["name"] for column in inspect(connection).get_columns("shops")}
            if "owner_restore_role_id" in shop_columns:
                connection.execute(text(
                "UPDATE shops SET owner_restore_role_id = NULL, pending_owner_proposal_id = NULL, "
                "pending_owner_from_user_id = NULL, pending_owner_target_user_id = NULL, "
                "last_owner_transfer_kind = NULL, last_owner_transfer_outcome = NULL, "
                "last_owner_transfer_reason = NULL, last_owner_transfer_from_user_id = NULL, "
                "last_owner_transfer_to_user_id = NULL, last_owner_transfer_at = NULL"
                ))
            connection.execute(text("DELETE FROM audit_events"))
            connection.execute(text("DELETE FROM stock_movements"))
            connection.execute(text("DELETE FROM order_items"))
            connection.execute(text("DELETE FROM orders"))
            connection.execute(text("DELETE FROM stocks"))
            connection.execute(text("DELETE FROM warehouse_accesses"))
            connection.execute(text("DELETE FROM warehouse_access_applications"))
            connection.execute(text("DELETE FROM product_offshelf_applications"))
            connection.execute(text("DELETE FROM skus"))
            connection.execute(text("DELETE FROM spus"))
            connection.execute(text("DELETE FROM categories"))
            connection.execute(text("DELETE FROM employee_applications"))
            connection.execute(text("DELETE FROM shop_employees"))
            connection.execute(text("DELETE FROM shop_employee_roles"))
            connection.execute(text("DELETE FROM shop_applications"))
            connection.execute(text("DELETE FROM merchant_applications"))
            connection.execute(text("DELETE FROM warehouses"))
            connection.execute(text("DELETE FROM shops"))
            connection.execute(text("DELETE FROM merchants"))
            connection.execute(text("DELETE FROM users"))
        yield engine, settings, server_version, database_name
    finally:
        try:
            if safe_to_cleanup:
                current_tables = set(inspect(engine).get_table_names())
                with engine.begin() as connection:
                    if "shops" in current_tables:
                        shop_columns = {column["name"] for column in inspect(connection).get_columns("shops")}
                        if "owner_restore_role_id" in shop_columns:
                            connection.execute(text(
                            "UPDATE shops SET owner_restore_role_id = NULL, pending_owner_proposal_id = NULL, "
                            "pending_owner_from_user_id = NULL, pending_owner_target_user_id = NULL, "
                            "last_owner_transfer_kind = NULL, last_owner_transfer_outcome = NULL, "
                            "last_owner_transfer_reason = NULL, last_owner_transfer_from_user_id = NULL, "
                            "last_owner_transfer_to_user_id = NULL, last_owner_transfer_at = NULL"
                            ))
                    for table_name in (
                        "audit_events", "stock_movements", "order_items", "orders", "stocks", "warehouse_accesses", "warehouse_access_applications", "product_offshelf_applications", "skus", "spus", "categories", "employee_applications", "shop_employees",
                        "shop_employee_roles", "shop_applications", "merchant_applications", "warehouses", "shops",
                        "merchants", "users",
                    ):
                        if table_name in current_tables:
                            connection.execute(text(f"DELETE FROM `{table_name}`"))
        finally:
            try:
                if session_caches_ready:
                    from app.db import session as db_session

                    db_session.get_session_factory.cache_clear()
                    cached_engine = db_session.get_engine()
                    cached_engine.dispose()
                    db_session.get_engine.cache_clear()
                    db_session.get_session_factory.cache_clear()
            finally:
                session_settings_patch.stop()
                engine.dispose()


@pytest.fixture
def mysql_test_session_factory(mysql_test_database, monkeypatch):
    from sqlalchemy.orm import sessionmaker

    engine, _settings, _version, _database_name = mysql_test_database
    from app.db import session as db_session

    monkeypatch.setattr(db_session, "get_settings", lambda: mysql_test_database[1])
    db_session.get_engine.cache_clear()
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    monkeypatch.setattr(db_session, "get_session_factory", lambda: factory)
    yield factory
    tables = set(inspect(engine).get_table_names())
    with engine.begin() as connection:
        if "shops" in tables:
            shop_columns = {column["name"] for column in inspect(connection).get_columns("shops")}
            if "owner_restore_role_id" in shop_columns:
                connection.execute(text(
                "UPDATE shops SET owner_restore_role_id = NULL, pending_owner_proposal_id = NULL, "
                "pending_owner_from_user_id = NULL, pending_owner_target_user_id = NULL, "
                "last_owner_transfer_kind = NULL, last_owner_transfer_outcome = NULL, "
                "last_owner_transfer_reason = NULL, last_owner_transfer_from_user_id = NULL, "
                "last_owner_transfer_to_user_id = NULL, last_owner_transfer_at = NULL"
                ))
        for table_name in (
            "audit_events", "stock_movements", "order_items", "orders", "stocks", "warehouse_accesses", "warehouse_access_applications", "product_offshelf_applications", "skus", "spus", "categories", "employee_applications", "shop_employees", "shop_employee_roles",
            "shop_applications", "merchant_applications", "warehouses", "shops", "merchants", "users"
        ):
            if table_name in tables:
                connection.execute(text(f"DELETE FROM {table_name}"))
