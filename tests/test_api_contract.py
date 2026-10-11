import httpx

from app.main import app


def test_openapi_essential_operations_and_security():
    schema = app.openapi()
    for path in [
        "/auth/token",
        "/accounts",
        "/cards/{card_id}/purchases",
        "/transactions",
        "/invoices/{invoice_id}/payments",
        "/budgets",
        "/reports/economic",
        "/reports/forecast",
    ]:
        assert path in schema["paths"]
    operation = schema["paths"]["/invoices/{invoice_id}/payments"]["post"]
    assert operation["security"] == [{"OAuth2PasswordBearer": []}]
    headers = {p["name"] for p in operation["parameters"] if p["in"] == "header" and p["required"]}
    assert {"X-Family-ID", "Idempotency-Key"} <= headers
    assert "InvoicePaymentView" in str(operation["responses"]["201"])


async def test_health_endpoint():
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/health")
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}
