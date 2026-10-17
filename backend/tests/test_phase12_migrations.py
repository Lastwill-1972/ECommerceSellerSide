import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import inspect, text

from app.core.config import BACKEND_DIR


def test_phase12_migration_guards_handoff_data_before_any_ddl(mysql_test_database):
    engine, _settings, _version, _database_name = mysql_test_database
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    with engine.begin() as connection:
        admin = connection.execute(text(
            "INSERT INTO users (username,email,password_hash) VALUES ('p12m-admin','p12m-admin@example.com','x')"
        )).lastrowid
        owner = connection.execute(text(
            "INSERT INTO users (username,email,password_hash) VALUES ('p12m-owner','p12m-owner@example.com','x')"
        )).lastrowid
        target = connection.execute(text(
            "INSERT INTO users (username,email,password_hash) VALUES ('p12m-target','p12m-target@example.com','x')"
        )).lastrowid
        merchant = connection.execute(text(
            "INSERT INTO merchants (name,admin_user_id) VALUES ('phase12 migration merchant',:admin)"
        ), {"admin": admin}).lastrowid
        shop = connection.execute(text(
            "INSERT INTO shops (merchant_id,name,owner_user_id) VALUES (:merchant,'phase12 migration shop',:owner)"
        ), {"merchant": merchant, "owner": owner}).lastrowid
        role = connection.execute(text(
            "INSERT INTO shop_employee_roles (shop_id,name) VALUES (:shop,'restore role')"
        ), {"shop": shop}).lastrowid
        connection.execute(text(
            "UPDATE shops SET owner_restore_role_id=:role,pending_owner_proposal_id='00000000-0000-0000-0000-000000000001', "
            "pending_owner_from_user_id=:owner,pending_owner_target_user_id=:target,last_owner_transfer_kind='FORCE', "
            "last_owner_transfer_outcome='FORCED',last_owner_transfer_reason='retained reason', "
            "last_owner_transfer_from_user_id=:owner,last_owner_transfer_to_user_id=:target,last_owner_transfer_at=CURRENT_TIMESTAMP "
            "WHERE id=:shop"
        ), {"role": role, "owner": owner, "target": target, "shop": shop})
    with pytest.raises(RuntimeError, match="refusing phase12 downgrade"):
        command.downgrade(config, "20261017_orders")
    assert {column["name"] for column in inspect(engine).get_columns("shops")} >= {
        "owner_restore_role_id", "pending_owner_proposal_id", "last_owner_transfer_reason"
    }
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == "20261017_phase12"
        assert connection.scalar(text("SELECT last_owner_transfer_reason FROM shops WHERE id=:id"), {"id": shop}) == "retained reason"
    with engine.begin() as connection:
        connection.execute(text(
            "UPDATE shops SET owner_restore_role_id=NULL,pending_owner_proposal_id=NULL,pending_owner_from_user_id=NULL, "
            "pending_owner_target_user_id=NULL,last_owner_transfer_kind=NULL,last_owner_transfer_outcome=NULL, "
            "last_owner_transfer_reason=NULL,last_owner_transfer_from_user_id=NULL,last_owner_transfer_to_user_id=NULL, "
            "last_owner_transfer_at=NULL WHERE id=:id"
        ), {"id": shop})
        connection.execute(text("DELETE FROM shop_employee_roles WHERE shop_id=:id"), {"id": shop})
        connection.execute(text("DELETE FROM shops WHERE id=:id"), {"id": shop})
        connection.execute(text("DELETE FROM merchants WHERE id=:id"), {"id": merchant})
        connection.execute(text("DELETE FROM users WHERE id IN (:admin,:owner,:target)"),
                           {"admin": admin, "owner": owner, "target": target})
