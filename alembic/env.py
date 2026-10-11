import asyncio
import os

from sqlalchemy import pool
from sqlalchemy.ext.asyncio import create_async_engine

from alembic import context
from app.db import database_connection
from app.models import Base

url, connect_args = database_connection(
    os.environ.get("MIGRATION_DATABASE_URL") or os.environ["DATABASE_URL"]
)
if "-pooler." in (url.host or ""):
    raise ValueError("Use a conexão direta do Neon, sem -pooler, para MIGRATION_DATABASE_URL.")


def run(connection):
    context.configure(connection=connection, target_metadata=Base.metadata, compare_type=True)
    with context.begin_transaction():
        context.run_migrations()


async def online():
    engine = create_async_engine(url, connect_args=connect_args, poolclass=pool.NullPool)
    async with engine.connect() as conn:
        await conn.run_sync(run)
    await engine.dispose()


if context.is_offline_mode():
    context.configure(
        url=url,
        target_metadata=Base.metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()
else:
    asyncio.run(online())
