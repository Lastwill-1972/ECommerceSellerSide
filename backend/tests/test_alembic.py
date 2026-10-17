import importlib
from io import StringIO

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy.orm import configure_mappers

from app.core.config import BACKEND_DIR
from app.db.base import Base


def test_model_registry_imports_core_identity_models_and_relationships():
    registry = importlib.import_module("app.db.models")
    configure_mappers()
    assert len(registry.__all__) == 8
    assert set(Base.metadata.tables) == {
        "users", "merchants", "shops", "merchant_applications", "warehouses", "audit_events",
        "shop_applications", "shop_employee_roles", "shop_employees", "employee_applications",
        "categories", "spus", "skus", "product_offshelf_applications",
        "warehouse_access_applications", "warehouse_accesses", "stocks", "stock_movements", "orders", "order_items",
    }
    assert Base.metadata.tables["users"].c.id.type.__class__.__name__ == "BigInteger"
    assert {constraint.name for constraint in Base.metadata.tables["shops"].constraints} >= {
        "uq_shops_merchant_name",
        "uq_shops_merchant_id",
    }
    assert {constraint.name for constraint in Base.metadata.tables["warehouses"].constraints} >= {
        "uq_warehouses_merchant_id_id",
    }


def test_migration_configuration_and_offline_sql(monkeypatch):
    # Offline only: synthetic credentials, no network access or schema changes.
    monkeypatch.setenv("DB_NAME", "offline_test")
    monkeypatch.setenv("DB_USER", "offline_test")
    monkeypatch.setenv("DB_PASSWORD", "offline%test")
    config = Config(str(BACKEND_DIR / "alembic.ini"), output_buffer=StringIO())
    scripts = ScriptDirectory.from_config(config)
    assert scripts.get_heads() == ["20261017_phase12"]
    command.upgrade(config, "head", sql=True)
