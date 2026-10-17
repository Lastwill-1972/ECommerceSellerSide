from decimal import Decimal

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import inspect, select, text

from app.core.config import BACKEND_DIR
from app.modules.catalog.models import Category, Sku, Spu
from app.modules.inventory.models import Stock, StockMovement, Warehouse
from app.modules.merchants.models import Merchant
from app.modules.orders.models import Order, OrderItem
from app.modules.shops.models import Shop
from app.modules.users.models import User


def test_orders_migration_preserves_manual_history_and_refuses_order_data_downgrade(
    mysql_test_session_factory,
    mysql_test_database,
):
    engine = mysql_test_database[0]
    factory = mysql_test_session_factory
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    with factory.begin() as session:
        admin = User(username="orders-migration-admin", email="orders-migration-admin@example.test", password_hash="x")
        owner = User(username="orders-migration-owner", email="orders-migration-owner@example.test", password_hash="x")
        session.add_all([admin, owner])
        session.flush()
        merchant = Merchant(name="Orders migration merchant", admin_user_id=admin.id)
        session.add(merchant)
        session.flush()
        warehouse = Warehouse(merchant_id=merchant.id, name="Migration warehouse")
        session.add(warehouse)
        shop = Shop(merchant_id=merchant.id, name="Migration shop", owner_user_id=owner.id)
        session.add(shop)
        category = Category(merchant_id=merchant.id, name="Migration category")
        session.add(category)
        session.flush()
        spu = Spu(merchant_id=merchant.id, category_id=category.id, name="Migration SPU", status="ON_SALE")
        session.add(spu)
        session.flush()
        sku = Sku(merchant_id=merchant.id, spu_id=spu.id, sku_code="MIGRATION-1",
                  specification="test", price=Decimal("12.30"))
        session.add(sku)
        session.flush()
        stock = Stock(merchant_id=merchant.id, warehouse_id=warehouse.id, sku_id=sku.id, quantity=1)
        session.add(stock)
        session.flush()
        movement = StockMovement(
            merchant_id=merchant.id, warehouse_id=warehouse.id, sku_id=sku.id,
            actor_user_id=admin.id, operation_type="MANUAL_ADJUSTMENT", quantity_delta=1,
            quantity_before=0, quantity_after=1, idempotency_key="migration-manual-history",
            request_hash="a" * 64,
        )
        session.add(movement)
        session.flush()
        movement_id = movement.id

    with engine.connect() as connection:
        original_manual = connection.execute(text(
            "SELECT id, merchant_id, warehouse_id, sku_id, actor_user_id, operation_type, "
            "quantity_delta, quantity_before, quantity_after, idempotency_key, request_hash, created_at "
            "FROM stock_movements WHERE id = :id"
        ), {"id": movement_id}).one()

    # One-stage downgrade/up must not touch historical manual rows.
    command.downgrade(config, "20261017_inventory")
    assert "order_item_id" not in {column["name"] for column in inspect(engine).get_columns("stock_movements")}
    command.upgrade(config, "head")
    with engine.connect() as connection:
        preserved_manual = connection.execute(text(
            "SELECT id, merchant_id, warehouse_id, sku_id, actor_user_id, operation_type, "
            "quantity_delta, quantity_before, quantity_after, idempotency_key, request_hash, created_at "
            "FROM stock_movements WHERE id = :id"
        ), {"id": movement_id}).one()
        assert preserved_manual == original_manual
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == "20261017_phase12"
    check_names = {row["name"] for row in inspect(engine).get_check_constraints("stock_movements")}
    assert check_names == {
        "ck_stock_movements_delta_matches_quantities",
        "ck_stock_movements_quantities_nonnegative",
        "ck_stock_movements_quantity_delta_nonzero",
        "ck_stock_movements_movement_origin_consistent",
        "ck_stock_movements_operation_type",
    }

    # Real order history blocks down migration before any DDL can remove it.
    with factory.begin() as session:
        order = Order(merchant_id=merchant.id, shop_id=shop.id, created_by_user_id=owner.id,
                      status="PENDING", total_amount=Decimal("12.30"), request_key="guarded-order",
                      request_hash="b" * 64)
        session.add(order)
        session.flush()
        item = OrderItem(order_id=order.id, merchant_id=merchant.id, sku_id=sku.id, quantity=1,
                         unit_price=Decimal("12.30"), subtotal=Decimal("12.30"),
                         sku_code_snapshot=sku.sku_code, spu_name_snapshot=spu.name,
                         specification_snapshot=sku.specification)
        session.add(item)
        session.flush()
    table_names_before = set(inspect(engine).get_table_names())
    check_names_before = {row["name"] for row in inspect(engine).get_check_constraints("stock_movements")}
    with pytest.raises(RuntimeError, match="cannot downgrade 20261017_orders"):
        command.downgrade(config, "20261017_inventory")
    assert set(inspect(engine).get_table_names()) == table_names_before
    assert {row["name"] for row in inspect(engine).get_check_constraints("stock_movements")} == check_names_before
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == "20261017_orders"
        assert connection.scalar(text("SELECT COUNT(*) FROM orders")) == 1
        assert connection.scalar(text("SELECT COUNT(*) FROM order_items")) == 1
        assert connection.scalar(text("SELECT idempotency_key FROM stock_movements WHERE id = :id"),
                                 {"id": movement_id}) == "migration-manual-history"

    # The guarded downgrade deliberately leaves the database at orders. Restore the
    # suite's phase12 schema before later tests reuse this session-scoped database.
    command.upgrade(config, "head")

