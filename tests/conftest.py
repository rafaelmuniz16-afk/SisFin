import os
from uuid import uuid4

import httpx
import pytest
import pytest_asyncio


@pytest_asyncio.fixture
async def api():
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("Integração requer TEST_DATABASE_URL apontando para PostgreSQL 16 migrado.")
    os.environ["DATABASE_URL"] = url
    os.environ.setdefault("JWT_SECRET", "test-only-" + "a" * 64)
    from app.config import settings
    from app.db import engine, sessions
    from app.main import app

    settings.cache_clear()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        yield client
    await engine().dispose()
    engine.cache_clear()
    sessions.cache_clear()


@pytest_asyncio.fixture
async def family(api):
    email = f"test-{uuid4()}@example.com"
    password = "testing-password-12345"
    response = await api.post(
        "/auth/register", json={"email": email, "password": password, "name": "Tester"}
    )
    assert response.status_code == 201, response.text
    user = response.json()
    token = await api.post("/auth/token", data={"username": email, "password": password})
    assert token.status_code == 200, token.text
    api.headers["Authorization"] = "Bearer " + token.json()["access_token"]
    family = await api.post("/auth/families", json={"name": "Família de teste"})
    assert family.status_code == 201, family.text
    api.headers["X-Family-ID"] = family.json()["id"]

    async def write(path, payload, key=None, method="POST", expected=201):
        response = await api.request(
            method, path, json=payload, headers={"Idempotency-Key": key or str(uuid4())}
        )
        assert response.status_code == expected, response.text
        return response.json()

    acc = await write(
        "/accounts",
        {
            "name": "Banco",
            "kind": "checking",
            "opening_balance": "5000.00",
            "opening_date": "2026-01-01",
        },
    )
    cat = await write("/categories", {"name": "Compras", "kind": "expense"})
    card = await write(
        "/cards",
        {
            "name": "Cartão",
            "credit_limit": "2000.00",
            "closing_day": 2,
            "due_day": 10,
            "payment_account_id": acc["id"],
        },
    )
    return {
        "write": write,
        "account": acc,
        "category": cat,
        "card": card,
        "family": family.json(),
        "user": user,
    }
