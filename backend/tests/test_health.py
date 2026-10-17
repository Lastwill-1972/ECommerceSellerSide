from fastapi.testclient import TestClient

from app.main import create_app


def test_health_without_database(monkeypatch):
    from app.db import session

    def forbid_database():
        raise AssertionError("Liveness must not initialize the database")

    monkeypatch.setattr(session, "get_engine", forbid_database)
    with TestClient(create_app()) as client:
        response = client.get("/api/v1/health")
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}


def test_openapi_exposes_current_health_auth_merchant_shop_and_employee_apis():
    with TestClient(create_app()) as client:
        schema = client.get("/openapi.json").json()
        assert set(schema["paths"]) == {
            "/api/v1/health",
            "/api/v1/auth/register",
            "/api/v1/auth/login",
            "/api/v1/auth/me",
            "/api/v1/merchants",
            "/api/v1/merchants/me",
            "/api/v1/merchants/applications",
            "/api/v1/merchants/applications/{application_id}/review",
            "/api/v1/shops",
            "/api/v1/shops/{shop_id}/owner",
            "/api/v1/shops/applications",
            "/api/v1/shops/applications/{application_id}/review",
            "/api/v1/employees/applications",
            "/api/v1/employees/applications/{application_id}/review",
            "/api/v1/shops/{shop_id}/employees",
            "/api/v1/shops/{shop_id}/employees/{user_id}",
            "/api/v1/shops/{shop_id}/employee-roles",
            "/api/v1/shops/{shop_id}/employees/{user_id}/role",
            "/api/v1/catalog/categories",
            "/api/v1/catalog/spus",
            "/api/v1/catalog/spus/{spu_id}",
            "/api/v1/catalog/spus/{spu_id}/publish",
            "/api/v1/catalog/spus/{spu_id}/skus",
            "/api/v1/catalog/skus/{sku_id}",
            "/api/v1/catalog/offshelf-applications",
            "/api/v1/catalog/offshelf-applications/{application_id}/review",
            "/api/v1/inventory/access-applications",
            "/api/v1/inventory/access-applications/{application_id}/review",
            "/api/v1/inventory/warehouse",
            "/api/v1/inventory/stocks",
            "/api/v1/inventory/movements",
            "/api/v1/inventory/operations",
            "/api/v1/orders",
            "/api/v1/orders/{order_id}",
            "/api/v1/orders/{order_id}/complete",
            "/api/v1/orders/{order_id}/cancel",
            "/api/v1/audit/events",
        }
        assert schema["paths"]["/api/v1/health"]["get"]["responses"]["200"]
        assert schema["paths"]["/api/v1/auth/register"]["post"]["responses"]["201"]
        assert schema["paths"]["/api/v1/auth/login"]["post"]["responses"]["200"]
        assert schema["paths"]["/api/v1/auth/me"]["get"]["responses"]["200"]
        assert schema["paths"]["/api/v1/merchants/applications"]["post"]["responses"]["201"]
        assert schema["paths"]["/api/v1/merchants/applications"]["get"]["responses"]["200"]
        assert schema["paths"]["/api/v1/merchants/applications/{application_id}/review"]["post"]["responses"]["200"]
        assert schema["paths"]["/api/v1/merchants"]["get"]["responses"]["200"]
        assert schema["paths"]["/api/v1/shops"]["get"]["responses"]["200"]
        assert schema["paths"]["/api/v1/shops/applications"]["post"]["responses"]["201"]
        assert schema["paths"]["/api/v1/catalog/categories"]["post"]["responses"]["201"]
        assert schema["paths"]["/api/v1/catalog/spus"]["post"]["responses"]["201"]
        assert schema["paths"]["/api/v1/catalog/spus/{spu_id}/skus"]["post"]["responses"]["201"]
        assert schema["paths"]["/api/v1/catalog/offshelf-applications"]["post"]["responses"]["201"]
        assert schema["paths"]["/api/v1/shops/applications"]["get"]["responses"]["200"]
        assert schema["paths"]["/api/v1/shops/applications/{application_id}/review"]["post"]["responses"]["200"]
        assert schema["paths"]["/api/v1/inventory/operations"]["post"]["responses"]["200"]
        assert schema["paths"]["/api/v1/orders"]["post"]["responses"]["201"]
        assert schema["paths"]["/api/v1/orders"]["get"]["responses"]["200"]
        assert schema["paths"]["/api/v1/orders/{order_id}"]["get"]["responses"]["200"]
        assert schema["paths"]["/api/v1/orders/{order_id}/complete"]["post"]["responses"]["200"]
        assert schema["paths"]["/api/v1/orders/{order_id}/cancel"]["post"]["responses"]["200"]
        assert schema["paths"]["/api/v1/merchants/me"]["patch"]["responses"]["200"]
        assert schema["paths"]["/api/v1/shops/{shop_id}/owner"]["put"]["responses"]["200"]
        assert schema["paths"]["/api/v1/audit/events"]["get"]["responses"]["200"]
