import secrets
from datetime import datetime, timedelta, timezone

import jwt
import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select, update

from app.core.config import get_settings
from app.main import create_app
from app.core.permissions import AccessDenied, AuthorizationService, OrderAction
from app.api.deps import require_order_action
from app.modules.merchants.models import Merchant
from app.modules.shops.models import Shop
from app.modules.users.models import User
from app.services.identity import IdentityConflict, IdentityEligibilityError, IdentityService, Role


@pytest.fixture
def auth_client(mysql_test_session_factory, monkeypatch):
    monkeypatch.setenv("AUTH_JWT_SECRET", secrets.token_urlsafe(48))
    monkeypatch.setenv("AUTH_JWT_ACCESS_TOKEN_EXPIRE_MINUTES", "30")
    get_settings.cache_clear()
    with TestClient(create_app()) as client:
        yield client, mysql_test_session_factory
    get_settings.cache_clear()


def register(client, username=None, email=None, password="long-correct-password"):
    suffix = secrets.token_hex(8)
    username = username or f"user-{suffix}"
    email = email or f"{suffix}@example.com"
    response = client.post(
        "/api/v1/auth/register",
        json={"username": username, "email": email, "password": password},
    )
    return response, username, email


def login(client, identifier, password="long-correct-password"):
    return client.post("/api/v1/auth/login", json={"identifier": identifier, "password": password})


def test_register_validates_and_hashes_password_and_rejects_duplicates(auth_client):
    client, factory = auth_client
    response, username, email = register(client)
    assert response.status_code == 201
    assert set(response.json()) == {"id", "username", "email"}
    assert "password" not in response.json() and "password_hash" not in response.json()
    with factory() as session:
        stored = session.scalar(select(User).where(User.id == response.json()["id"]))
        assert stored is not None
        assert stored.password_hash.startswith("$argon2id$")

    assert client.post(
        "/api/v1/auth/register",
        json={"username": username.upper(), "email": f"other-{email}", "password": "long-correct-password"},
    ).status_code == 409
    assert client.post(
        "/api/v1/auth/register",
        json={"username": f"other-{username}", "email": email.upper(), "password": "long-correct-password"},
    ).status_code == 409
    assert client.post(
        "/api/v1/auth/register",
        json={"username": "x", "email": "not-an-email", "password": "short", "is_platform_admin": True},
    ).status_code == 422


def test_login_me_wrong_password_and_non_admin_has_no_management_rights(auth_client):
    client, factory = auth_client
    response, username, email = register(client)
    assert response.status_code == 201
    assert login(client, username, "wrong-password").status_code == 401
    assert login(client, "missing@example.com").status_code == 401
    token_response = login(client, email)
    assert token_response.status_code == 200
    payload = token_response.json()
    assert payload["token_type"] == "bearer"
    assert payload["user"]["id"] == response.json()["id"]
    with factory() as session:
        user = session.get(User, response.json()["id"])
        assert user is not None
        identity = IdentityService.get_identity(session, user)
        assert identity.role is Role.NONE
        with pytest.raises(AccessDenied):
            AuthorizationService.require_platform_admin(identity)
        with pytest.raises(AccessDenied):
            AuthorizationService.check_merchant_management(identity, 1)
        with pytest.raises(AccessDenied):
            AuthorizationService.check_shop(session, identity, 1)

    me = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {payload['access_token']}"})
    assert me.status_code == 200
    assert me.json()["role"] == "NONE"
    assert me.json()["merchant_id"] is None and me.json()["shop_id"] is None


def test_missing_malformed_tampered_expired_and_disabled_tokens_are_401(auth_client, monkeypatch):
    client, factory = auth_client
    response, username, _email = register(client)
    token = login(client, username).json()["access_token"]

    invalid_headers = [
        {},
        {"Authorization": "Basic abc"},
        {"Authorization": "Bearer not.a.jwt"},
        {"Authorization": f"Bearer {token[:-2]}xx"},
    ]
    for headers in invalid_headers:
        rejected = client.get("/api/v1/auth/me", headers=headers)
        assert rejected.status_code == 401
        assert rejected.headers["www-authenticate"] == "Bearer"

    key = get_settings().auth_jwt_secret.get_secret_value()
    malformed_tokens = [
        jwt.encode({"sub": "not-an-id", "exp": datetime.now(timezone.utc) + timedelta(minutes=5)}, key, algorithm="HS256"),
        jwt.encode({"sub": str(response.json()["id"]), "exp": datetime.now(timezone.utc) + timedelta(minutes=5)}, "", algorithm="none"),
    ]
    for invalid_token in malformed_tokens:
        rejected = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {invalid_token}"})
        assert rejected.status_code == 401
        assert rejected.headers["www-authenticate"] == "Bearer"

    expired = jwt.encode(
        {"sub": str(response.json()["id"]), "exp": datetime.now(timezone.utc) - timedelta(minutes=1)},
        key,
        algorithm="HS256",
    )
    expired_response = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {expired}"})
    assert expired_response.status_code == 401
    assert expired_response.headers["www-authenticate"] == "Bearer"

    with factory.begin() as session:
        session.execute(update(User).where(User.id == response.json()["id"]).values(is_active=False))
    disabled_response = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert disabled_response.status_code == 401
    assert disabled_response.headers["www-authenticate"] == "Bearer"
    assert login(client, username).status_code == 401


def test_missing_or_weak_jwt_secret_fails_closed(auth_client, monkeypatch):
    client, _factory = auth_client
    _response, username, _email = register(client)
    monkeypatch.setenv("AUTH_JWT_SECRET", "too-short")
    get_settings.cache_clear()
    rejected_login = login(client, username)
    assert rejected_login.status_code == 503
    assert "AUTH_JWT_SECRET" not in rejected_login.text
    rejected_me = client.get("/api/v1/auth/me", headers={"Authorization": "Bearer malformed"})
    assert rejected_me.status_code == 401
    assert rejected_me.headers["www-authenticate"] == "Bearer"


def test_identity_is_reloaded_and_token_role_or_tenant_claims_are_ignored(auth_client):
    client, factory = auth_client
    response, username, _email = register(client)
    token = login(client, username).json()["access_token"]
    user_id = response.json()["id"]

    with factory.begin() as session:
        user = session.get(User, user_id)
        merchant = Merchant(name=f"merchant-{secrets.token_hex(6)}", admin_user=user)
        session.add(merchant)
        session.flush()
        merchant_id = merchant.id

    headers = {"Authorization": f"Bearer {token}"}
    me = client.get("/api/v1/auth/me", headers=headers)
    assert me.status_code == 200
    assert me.json()["role"] == "MERCHANT_ADMIN"
    assert me.json()["merchant_id"] == merchant_id

    key = get_settings().auth_jwt_secret.get_secret_value()
    forged = jwt.encode(
        {
            "sub": str(user_id),
            "exp": datetime.now(timezone.utc) + timedelta(minutes=5),
            "role": "PLATFORM_ADMIN",
            "merchant_id": merchant_id + 100,
            "shop_id": 999999,
        },
        key,
        algorithm="HS256",
    )
    forged_me = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {forged}"})
    assert forged_me.status_code == 200
    assert forged_me.json()["role"] == "MERCHANT_ADMIN"
    assert forged_me.json()["merchant_id"] == merchant_id


def test_identity_derivation_rejects_all_mutually_exclusive_relationship_conflicts(auth_client):
    client, factory = auth_client
    users = [User(username=f"identity-{secrets.token_hex(8)}", email=f"{secrets.token_hex(8)}@example.com", password_hash="unused") for _ in range(5)]
    with factory.begin() as session:
        session.add_all(users)
        session.flush()
        admin, owner, platform_merchant, platform_shop, merchant_shop = users
        platform_admin = User(username=f"platform-{secrets.token_hex(8)}", email=f"{secrets.token_hex(8)}@example.com", password_hash="unused", is_platform_admin=True)
        session.add(platform_admin)
        merchant_one = Merchant(name=f"m1-{secrets.token_hex(6)}", admin_user=admin)
        merchant_two = Merchant(name=f"m2-{secrets.token_hex(6)}", admin_user=platform_merchant)
        merchant_three = Merchant(name=f"m3-{secrets.token_hex(6)}", admin_user=merchant_shop)
        session.add_all([merchant_one, merchant_two, merchant_three])
        session.flush()
        shop_one = Shop(name="shop-one", merchant=merchant_one, owner_user=owner)
        shop_two = Shop(name="shop-two", merchant=merchant_two, owner_user=platform_shop)
        shop_three = Shop(name="shop-three", merchant=merchant_three, owner_user=merchant_shop)
        session.add_all([shop_one, shop_two, shop_three])
        platform_shop.is_platform_admin = True
        platform_merchant.is_platform_admin = True
        eligible = User(username=f"eligible-{secrets.token_hex(6)}", email=f"{secrets.token_hex(8)}@example.com", password_hash="unused")
        disabled = User(username=f"disabled-{secrets.token_hex(6)}", email=f"{secrets.token_hex(8)}@example.com", password_hash="unused", is_active=False)
        session.add_all([eligible, disabled])
        ids = {
            "platform": platform_admin.id,
            "merchant": admin.id,
            "owner": owner.id,
            "platform_shop": platform_shop.id,
            "platform_merchant": platform_merchant.id,
            "merchant_shop": merchant_shop.id,
        }

    with factory() as session:
        assert IdentityService.get_identity(session, session.get(User, ids["platform"])).role is Role.PLATFORM_ADMIN
        assert IdentityService.get_identity(session, session.get(User, ids["merchant"])).role is Role.MERCHANT_ADMIN
        owner_identity = IdentityService.get_identity(session, session.get(User, ids["owner"]))
        assert owner_identity.role is Role.SHOP_OWNER
        with pytest.raises(IdentityConflict):
            IdentityService.get_identity(session, session.get(User, ids["platform_shop"]))
        with pytest.raises(IdentityConflict):
            IdentityService.get_identity(session, session.get(User, ids["platform_merchant"]))
        with pytest.raises(IdentityConflict):
            IdentityService.get_identity(session, session.get(User, ids["merchant_shop"]))
        eligible_user = session.scalar(select(User).where(User.username == eligible.username))
        assert IdentityService.ensure_eligible_for_merchant_admin(session, eligible_user.id) is eligible_user
        assert IdentityService.ensure_eligible_for_shop_owner(session, eligible_user.id) is eligible_user
        with pytest.raises(IdentityEligibilityError):
            IdentityService.ensure_eligible_for_merchant_admin(session, disabled.id)
        with pytest.raises(IdentityEligibilityError):
            IdentityService.ensure_eligible_for_shop_owner(session, ids["merchant"])

    # The actual protected endpoint refuses an identity conflict too.
    conflict = User(username=f"conflict-{secrets.token_hex(8)}", email=f"{secrets.token_hex(8)}@example.com", password_hash="$argon2id$unused")
    with factory.begin() as session:
        merchant_admin = User(username=f"conflict-admin-{secrets.token_hex(6)}", email=f"{secrets.token_hex(8)}@example.com", password_hash="unused")
        merchant = Merchant(name=f"conflict-merchant-{secrets.token_hex(6)}", admin_user=merchant_admin)
        session.add(conflict)
        session.add(merchant)
        session.flush()
        conflict.is_platform_admin = True
        session.add(Shop(name=f"conflict-shop-{secrets.token_hex(4)}", merchant=merchant, owner_user=conflict))
        conflict_id = conflict.id
    # Sign-in is not needed: exercise the dependency using a locally signed identity token.
    key = get_settings().auth_jwt_secret.get_secret_value()
    conflict_token = jwt.encode(
        {"sub": str(conflict_id), "exp": datetime.now(timezone.utc) + timedelta(minutes=5)},
        key,
        algorithm="HS256",
    )
    rejected = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {conflict_token}"})
    assert rejected.status_code == 403


def test_tenant_scopes_resolve_shop_relationship_and_apply_fixed_order_actions(auth_client):
    _client, factory = auth_client
    with factory.begin() as session:
        merchant_admin = User(username=f"ma-{secrets.token_hex(8)}", email=f"{secrets.token_hex(8)}@example.com", password_hash="unused")
        other_admin = User(username=f"ma-{secrets.token_hex(8)}", email=f"{secrets.token_hex(8)}@example.com", password_hash="unused")
        owner = User(username=f"owner-{secrets.token_hex(8)}", email=f"{secrets.token_hex(8)}@example.com", password_hash="unused")
        other_owner = User(username=f"owner-{secrets.token_hex(8)}", email=f"{secrets.token_hex(8)}@example.com", password_hash="unused")
        platform = User(username=f"pa-{secrets.token_hex(8)}", email=f"{secrets.token_hex(8)}@example.com", password_hash="unused", is_platform_admin=True)
        session.add_all([merchant_admin, other_admin, owner, other_owner, platform])
        session.flush()
        merchant = Merchant(name=f"tenant-{secrets.token_hex(6)}", admin_user=merchant_admin)
        other_merchant = Merchant(name=f"tenant-{secrets.token_hex(6)}", admin_user=other_admin)
        session.add_all([merchant, other_merchant])
        session.flush()
        same_merchant_other_owner = User(username=f"owner-{secrets.token_hex(8)}", email=f"{secrets.token_hex(8)}@example.com", password_hash="unused")
        session.add(same_merchant_other_owner)
        session.flush()
        shop = Shop(name=f"shop-{secrets.token_hex(6)}", merchant=merchant, owner_user=owner)
        other_shop = Shop(name=f"shop-{secrets.token_hex(6)}", merchant=other_merchant, owner_user=other_owner)
        same_merchant_other_shop = Shop(name=f"shop-{secrets.token_hex(6)}", merchant=merchant, owner_user=same_merchant_other_owner)
        session.add_all([shop, other_shop, same_merchant_other_shop])
        session.flush()
        merchant_id, other_merchant_id, shop_id, other_shop_id = merchant.id, other_merchant.id, shop.id, other_shop.id
        same_merchant_other_shop_id = same_merchant_other_shop.id
        admin_user_id, other_admin_id_value, owner_id, platform_id = merchant_admin.id, other_admin.id, owner.id, platform.id

    with factory() as session:
        admin_identity = IdentityService.get_identity(session, session.get(User, admin_user_id))
        other_admin_identity = IdentityService.get_identity(session, session.get(User, other_admin_id_value))
        owner_identity = IdentityService.get_identity(session, session.get(User, owner_id))
        platform_identity = IdentityService.get_identity(session, session.get(User, platform_id))

        AuthorizationService.check_merchant_management(admin_identity, merchant_id)
        AuthorizationService.check_shop(session, admin_identity, shop_id)
        assert AuthorizationService.check_shop(session, admin_identity, same_merchant_other_shop_id) == merchant_id
        assert AuthorizationService.check_order_action(session, admin_identity, shop_id=shop_id, action=OrderAction.READ) == merchant_id
        for action in (OrderAction.CREATE, OrderAction.COMPLETE, OrderAction.CANCEL):
            with pytest.raises(AccessDenied):
                AuthorizationService.check_order_action(session, admin_identity, shop_id=shop_id, action=action)
        with pytest.raises(AccessDenied):
            AuthorizationService.check_merchant_management(admin_identity, other_merchant_id)
        with pytest.raises(AccessDenied):
            AuthorizationService.check_shop(session, admin_identity, other_shop_id)
        with pytest.raises(AccessDenied):
            AuthorizationService.check_shop(session, other_admin_identity, shop_id)
        with pytest.raises(AccessDenied):
            AuthorizationService.check_shop_management(session, admin_identity, shop_id)

        assert AuthorizationService.check_shop_management(session, owner_identity, shop_id) == merchant_id
        AuthorizationService.check_order_action(session, owner_identity, shop_id=shop_id, action=OrderAction.CANCEL)
        with pytest.raises(AccessDenied):
            AuthorizationService.check_shop(session, owner_identity, other_shop_id)
        with pytest.raises(AccessDenied):
            AuthorizationService.check_shop(session, owner_identity, same_merchant_other_shop_id)
        AuthorizationService.require_platform_admin(platform_identity)
        with pytest.raises(AccessDenied):
            AuthorizationService.check_shop(session, platform_identity, shop_id)
        with pytest.raises(AccessDenied):
            AuthorizationService.check_merchant_management(platform_identity, merchant_id)


def test_server_bound_order_action_cannot_be_downgraded_by_query_parameter(auth_client):
    _client, factory = auth_client
    with factory.begin() as session:
        admin_user = User(username=f"order-admin-{secrets.token_hex(6)}", email=f"{secrets.token_hex(8)}@example.com", password_hash="unused")
        owner = User(username=f"order-owner-{secrets.token_hex(6)}", email=f"{secrets.token_hex(8)}@example.com", password_hash="unused")
        session.add_all([admin_user, owner])
        session.flush()
        merchant = Merchant(name=f"order-merchant-{secrets.token_hex(6)}", admin_user=admin_user)
        session.add(merchant)
        session.flush()
        shop = Shop(name=f"order-shop-{secrets.token_hex(6)}", merchant=merchant, owner_user=owner)
        session.add(shop)
        session.flush()
        shop_id = shop.id
        admin_user_id = admin_user.id

    # Use a locally signed token to exercise the dependency with the merchant identity.
    key = get_settings().auth_jwt_secret.get_secret_value()
    merchant_token = jwt.encode(
        {"sub": str(admin_user_id), "exp": datetime.now(timezone.utc) + timedelta(minutes=5)},
        key,
        algorithm="HS256",
    )
    probe = FastAPI()

    @probe.post("/shops/{shop_id}/cancel", dependencies=[Depends(require_order_action(OrderAction.CANCEL))])
    def cancel_order():
        return {"cancelled": True}

    with TestClient(probe) as probe_client:
        result = probe_client.post(
            f"/shops/{shop_id}/cancel?action=read",
            headers={"Authorization": f"Bearer {merchant_token}"},
        )
    assert result.status_code == 403

