"""Contratos de deploy/CORS sem simular a contabilidade do PostgreSQL."""

import ssl
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from sqlalchemy.dialects import postgresql
from sqlalchemy.pool import NullPool

from app.db import database_connection, engine, get_db
from app.deps import current_user
from app.domain import Role
from app.main import app


@pytest.mark.parametrize("prefix", ["postgres://", "postgresql://", "postgresql+asyncpg://"])
def test_async_url_and_credentials(prefix):
    url, args = database_connection(
        prefix + "person:p%40ss%2Fword@ep-demo.neon.tech/db?sslmode=require&connect_timeout=17"
    )
    assert url.drivername == "postgresql+asyncpg"
    assert url.password == "p@ss/word"
    assert "sslmode" not in url.query
    assert args["timeout"] == 17
    assert isinstance(args["ssl"], ssl.SSLContext)
    assert args["ssl"].verify_mode == ssl.CERT_REQUIRED
    assert args["ssl"].check_hostname


def test_tls_defaults_and_local_opt_out():
    _, neon = database_connection("postgres://a:b@ep-demo.neon.tech/db")
    _, local = database_connection("postgresql://a:b@localhost/db?sslmode=disable")
    assert isinstance(neon["ssl"], ssl.SSLContext)
    assert local["ssl"] is False


@pytest.mark.parametrize(
    "url",
    [
        "sqlite:///data.db",
        "postgresql://a:b@ep-demo.neon.tech/db?sslmode=disable",
        "postgresql://a:b@localhost/db?sslmode=require&ssl=true",
        "postgresql://a:b@localhost/db?sslmode=prefer",
        "postgresql://a:b@ep-demo.neon.tech/db?channel_binding=require",
    ],
)
def test_rejects_incompatible_options(url):
    with pytest.raises(ValueError):
        database_connection(url)


def test_extra_options():
    url, args = database_connection(
        "postgres://a:b@localhost/db?application_name=sisfin&prepared_statement_cache_size=0"
    )
    assert args["server_settings"] == {"application_name": "sisfin"}
    assert dict(url.query) == {"prepared_statement_cache_size": "0"}


async def test_neon_pooler_uses_no_nested_pool(monkeypatch):
    monkeypatch.setattr(
        "app.db.settings",
        lambda: SimpleNamespace(database_url="postgres://a:b@ep-demo-pooler.us.neon.tech/db"),
    )
    engine.cache_clear()
    instance = engine()
    assert isinstance(instance.pool, NullPool)
    assert instance.url.query["prepared_statement_cache_size"] == "0"
    await instance.dispose()
    engine.cache_clear()


async def test_cors_bearer_and_family_preflight():
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="https://api.test"
    ) as client:
        origin = "https://interface.example"
        response = await client.options(
            "/invoices/any/payments",
            headers={
                "Origin": origin,
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": (
                    "Authorization, Content-Type, X-Family-ID, Idempotency-Key"
                ),
            },
        )
        assert response.status_code == 200
        assert response.headers["access-control-allow-origin"] == origin
        assert response.headers["access-control-allow-credentials"] == "true"
        allowed = response.headers["access-control-allow-headers"].lower()
        assert "authorization" in allowed
        assert "x-family-id" in allowed
        assert "idempotency-key" in allowed
        # Resposta real sem Cookie; a SPA usa credentials: "omit".
        health = await client.get("/health", headers={"Origin": origin})
        assert health.headers["access-control-allow-origin"] in ("*", origin)
        assert health.json() == {"status": "ok"}


async def test_frontend_is_served_from_api_root():
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="https://api.test"
    ) as client:
        response = await client.get("/")
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/html")
        assert response.headers["cache-control"] == "no-cache"
        assert 'id="auth-form"' in response.text
        assert "credentials:'omit'" in response.text
        assert "crypto.randomUUID()" in response.text


async def test_family_selector_returns_only_membership_query():
    user = SimpleNamespace(id=uuid4())
    family = SimpleNamespace(
        id=uuid4(), name="Família <teste>", currency="BRL", timezone="America/Sao_Paulo"
    )
    captured = []

    class DB:
        async def execute(self, query):
            captured.append(query.compile(dialect=postgresql.dialect()))
            return SimpleNamespace(all=lambda: [(family, Role.EDITOR)])

    app.dependency_overrides[current_user] = lambda: user
    app.dependency_overrides[get_db] = lambda: DB()
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="https://api.test"
        ) as client:
            response = await client.get("/auth/families")
        assert response.status_code == 200
        assert response.json() == [
            {
                "id": str(family.id),
                "name": family.name,
                "role": "editor",
                "currency": "BRL",
                "timezone": "America/Sao_Paulo",
            }
        ]
        assert user.id in captured[0].params.values()
        assert "WHERE family_members.user_id" in str(captured[0])
    finally:
        app.dependency_overrides.clear()
