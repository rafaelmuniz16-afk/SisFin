from contextlib import asynccontextmanager
from pathlib import Path
from uuid import UUID

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, OperationalError

from app import models as m
from app.db import engine
from app.deps import DB, CurrentUser
from app.domain import Role
from app.routers import accounts, auth, budgets, cards, invoices, planning, reports, transactions


@asynccontextmanager
async def lifespan(app):
    # Valida URL/driver/TLS ao iniciar, sem abrir uma conexão desnecessária.
    engine()
    yield
    await engine().dispose()


app = FastAPI(
    title="SisFin — ERP financeiro familiar",
    version="1.1.0",
    lifespan=lifespan,
    description="Valores monetários como strings decimais. JWT Bearer + X-Family-ID. "
    "Escritas financeiras exigem Idempotency-Key. Razão de partidas dobradas.",
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    max_age=600,
)
# A SPA envia JWT via Authorization e usa fetch(credentials="omit").
# Se cookies forem adotados, use origens explícitas em vez de wildcard.
for router in [
    auth.router,
    accounts.router,
    cards.router,
    transactions.router,
    invoices.router,
    budgets.router,
    reports.router,
    planning.router,
]:
    app.include_router(router)


@app.exception_handler(IntegrityError)
async def integrity_error(request: Request, error: IntegrityError):
    # Nunca expor SQL, parâmetros ou dados financeiros de outra família.
    return JSONResponse(
        status_code=409,
        content={"detail": "Operação viola uma restrição de integridade ou unicidade."},
    )


@app.get("/health", tags=["health"])
async def health():
    return {"status": "ok"}


class FamilyChoice(BaseModel):
    id: UUID
    name: str
    role: Role
    currency: str
    timezone: str


@app.get("/auth/families", tags=["auth"], response_model=list[FamilyChoice])
async def list_families(user: CurrentUser, db: DB):
    """Famílias autorizadas com nome, papel e fuso para o seletor da SPA."""
    rows = (
        await db.execute(
            select(m.Family, m.FamilyMember.role)
            .join(m.FamilyMember, m.FamilyMember.family_id == m.Family.id)
            .where(m.FamilyMember.user_id == user.id)
            .order_by(m.Family.name, m.Family.id)
        )
    ).all()
    return [
        {
            "id": str(family.id),
            "name": family.name,
            "role": role.value,
            "currency": family.currency,
            "timezone": family.timezone,
        }
        for family, role in rows
    ]


@app.exception_handler(OperationalError)
async def database_unavailable(request: Request, error: OperationalError):
    return JSONResponse(
        status_code=503,
        headers={"Retry-After": "5"},
        content={"detail": "Banco temporariamente indisponível. Aguarde e tente novamente."},
    )


@app.get("/", include_in_schema=False)
async def frontend():
    index = Path(__file__).resolve().parents[1] / "index.html"
    if index.is_file():
        return FileResponse(index, headers={"Cache-Control": "no-cache"})
    return JSONResponse({"service": "SisFin API", "docs": "/docs"})
