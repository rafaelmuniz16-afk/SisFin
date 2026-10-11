import ssl
from functools import lru_cache
from uuid import uuid4

from sqlalchemy.engine import URL, make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.config import settings


def database_connection(raw_url: str) -> tuple[URL, dict]:
    """Converte URLs do Neon/Render sem alterar usuário, senha ou database.

    sslmode é opção libpq, não argumento de asyncpg.connect. Usamos um contexto
    TLS validado. A mesma função é usada pela API e pelo Alembic.
    """
    raw_url = raw_url.strip()
    if raw_url.startswith("postgres://"):
        raw_url = "postgresql+asyncpg://" + raw_url[len("postgres://") :]
    elif raw_url.startswith("postgresql://"):
        raw_url = "postgresql+asyncpg://" + raw_url[len("postgresql://") :]
    url = make_url(raw_url)
    if url.drivername != "postgresql+asyncpg":
        raise ValueError("DATABASE_URL deve usar PostgreSQL com asyncpg.")
    query = dict(url.query)
    mode = query.pop("sslmode", None)
    ssl_option = query.pop("ssl", None)
    binding = query.pop("channel_binding", None)
    if binding not in (None, "disable"):
        # Não descartar silenciosamente uma exigência de segurança do usuário.
        raise ValueError(
            "asyncpg não oferece channel_binding nesta integração. Use uma URL compatível com asyncpg contendo sslmode=require e sem channel_binding, ou um driver que suporte essa exigência."
        )
    if mode and ssl_option:
        raise ValueError("Informe sslmode ou ssl, não ambos.")
    mode = mode or ssl_option
    neon = (url.host or "").endswith(".neon.tech")
    args = {"timeout": 30, "command_timeout": 60}
    if mode in ("require", "verify-full", "verify-ca", "true", "1") or (mode is None and neon):
        args["ssl"] = ssl.create_default_context()
    elif mode in ("disable", "false", "0"):
        if neon:
            raise ValueError("Conexões com o Neon exigem TLS.")
        args["ssl"] = False
    elif mode is not None:
        raise ValueError(
            "Modo SSL não suportado. Use sslmode=require (ou disable somente em banco local)."
        )
    if "connect_timeout" in query:
        args["timeout"] = int(query.pop("connect_timeout"))
    if "application_name" in query:
        args["server_settings"] = {"application_name": query.pop("application_name")}
    return url.set(query=query), args


@lru_cache
def engine():
    url, connect_args = database_connection(settings().database_url)
    pool_options = {"pool_size": 3, "max_overflow": 2, "pool_recycle": 300}
    if "-pooler." in (url.host or ""):
        # Evita empilhar pools e colisões de nomes em PgBouncer.
        pool_options = {"poolclass": NullPool}
        connect_args.update(
            statement_cache_size=0, prepared_statement_name_func=lambda: f"__sisfin_{uuid4().hex}__"
        )
        url = url.update_query_dict({"prepared_statement_cache_size": "0"})
    return create_async_engine(
        url,
        connect_args=connect_args,
        pool_pre_ping=True,
        isolation_level="READ COMMITTED",
        **pool_options,
    )


@lru_cache
def sessions():
    return async_sessionmaker(engine(), expire_on_commit=False, autoflush=False)


async def get_db():
    async with sessions()() as session:
        yield session
        # Escritas fazem commit explícito ANTES da resposta; close desfaz somente leituras/erros.
