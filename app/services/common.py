import hashlib
import json
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import func, select, text
from sqlalchemy.inspection import inspect

from app.deps import Context
from app.domain import ZERO, LedgerKind, Role
from app.models import FamilyMember, IdempotencyRecord, Journal, JournalLine, LedgerAccount


def public(value):
    """Decimal sai como string; impede conversão silenciosa do JSON encoder para float."""
    if isinstance(value, Decimal):
        return format(value, ".2f")
    if isinstance(value, (UUID, date, datetime)):
        return str(value)
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, dict):
        return {k: public(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [public(v) for v in value]
    if hasattr(value, "__table__"):
        return {c.key: public(getattr(value, c.key)) for c in inspect(value).mapper.column_attrs}
    return value


async def get(ctx: Context, model, identifier: UUID, *, lock=False):
    query = select(model).where(model.family_id == ctx.family_id, model.id == identifier)
    if lock:
        query = query.with_for_update()
    result = await ctx.db.scalar(query)
    if result is None:
        raise HTTPException(404, "Recurso não encontrado nesta família.")
    return result


async def add(ctx: Context, model, **values):
    row = model(family_id=ctx.family_id, **values)
    ctx.db.add(row)
    await ctx.db.flush()
    return row


def actual_date(ctx: Context, value: date):
    if value > ctx.today:
        raise HTTPException(
            422, "Eventos realizados não podem ter data futura; use recorrências/previsões."
        )


async def atomic(ctx: Context, key: str, operation: str, payload, action):
    # Granularidade intencional: uma família tem pouco volume. Serializa TODAS
    # as escritas financeiras, não só tentativas com a mesma chave.
    await ctx.db.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:family, 0))"),
        {"family": str(ctx.family_id)},
    )
    # Revalida após o lock: uma mudança concorrente de papel não passa com cache antigo.
    member = await ctx.db.scalar(
        select(FamilyMember)
        .where(FamilyMember.family_id == ctx.family_id, FamilyMember.user_id == ctx.actor_id)
        .execution_options(populate_existing=True)
    )
    if (
        not member
        or member.role == Role.VIEWER
        or (operation == "member.upsert" and member.role != Role.ADMIN)
    ):
        raise HTTPException(403, "Permissão alterada ou insuficiente.")
    body = payload.model_dump(mode="json") if hasattr(payload, "model_dump") else public(payload)
    digest = hashlib.sha256(
        json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    existing = await ctx.db.scalar(
        select(IdempotencyRecord).where(
            IdempotencyRecord.family_id == ctx.family_id, IdempotencyRecord.key == key
        )
    )
    if existing:
        if (existing.request_hash, existing.operation, existing.actor_id) != (
            digest,
            operation,
            ctx.actor_id,
        ):
            raise HTTPException(
                409, "Chave de idempotência já utilizada em outra operação/payload/usuário."
            )
        result = existing.response
    else:
        result = public(await action())
        await add(
            ctx,
            IdempotencyRecord,
            key=key,
            actor_id=ctx.actor_id,
            operation=operation,
            request_hash=digest,
            response=result,
        )
    # Constraints diferidas, erro de commit e rollback acontecem antes do HTTP 2xx.
    await ctx.db.commit()
    return result


async def system_account(ctx: Context, key: str) -> LedgerAccount:
    return await ctx.db.scalar(
        select(LedgerAccount).where(
            LedgerAccount.family_id == ctx.family_id, LedgerAccount.system_key == key
        )
    )


async def balance(ctx: Context, ledger_id: UUID, through: date | None = None) -> Decimal:
    query = select(func.coalesce(func.sum(JournalLine.debit - JournalLine.credit), 0)).where(
        JournalLine.family_id == ctx.family_id, JournalLine.ledger_account_id == ledger_id
    )
    if through:
        query = query.join(Journal, Journal.id == JournalLine.journal_id).where(
            Journal.effective_date <= through
        )
    return Decimal(await ctx.db.scalar(query))


async def journal(
    ctx: Context, transaction, event: str, economic: date, effective: date, lines: list
):
    # (ledger_id, debit, credit, category_id)
    if len(lines) < 2 or sum((x[1] - x[2] for x in lines), ZERO) != ZERO:
        raise ValueError("Partidas devem estar balanceadas.")
    entry = await add(
        ctx,
        Journal,
        transaction_id=transaction.id,
        event=event,
        economic_date=economic,
        effective_date=effective,
    )
    for account_id, debit, credit, category in lines:
        if debit < 0 or credit < 0 or (debit > 0) == (credit > 0):
            raise ValueError("Cada linha deve ter apenas débito ou crédito positivo.")
        await add(
            ctx,
            JournalLine,
            journal_id=entry.id,
            ledger_account_id=account_id,
            debit=debit,
            credit=credit,
            category_id=category,
        )
    return entry


async def ensure_funds(ctx, account, value, at=None):
    if not account.active:
        raise HTTPException(409, "Conta inativa.")
    if not account.allow_overdraft and await balance(ctx, account.ledger_account_id) < value:
        raise HTTPException(409, "Saldo insuficiente.")
    if not account.allow_overdraft and at:
        # Débito retroativo não pode tornar negativo o extrato histórico.
        rows = (
            await ctx.db.execute(
                select(Journal.effective_date, func.sum(JournalLine.debit - JournalLine.credit))
                .select_from(JournalLine)
                .join(Journal, Journal.id == JournalLine.journal_id)
                .where(
                    JournalLine.family_id == ctx.family_id,
                    JournalLine.ledger_account_id == account.ledger_account_id,
                )
                .group_by(Journal.effective_date)
                .order_by(Journal.effective_date)
            )
        ).all()
        running = ZERO
        checked_date = False
        for day, delta in rows:
            if day > at and not checked_date:
                if running < value:
                    raise HTTPException(409, "Saldo insuficiente na data informada.")
                checked_date = True
            running += delta
            if day >= at:
                checked_date = True
                if running < value:
                    raise HTTPException(
                        409, "Débito retroativo tornaria o saldo histórico negativo."
                    )
        if not checked_date and running < value:
            raise HTTPException(409, "Saldo insuficiente na data informada.")


async def initialize_ledger(ctx):
    for key, kind in [
        ("income", LedgerKind.INCOME),
        ("expense", LedgerKind.EXPENSE),
        ("equity", LedgerKind.EQUITY),
        ("payable", LedgerKind.LIABILITY),
        ("receivable", LedgerKind.ASSET),
    ]:
        await add(ctx, LedgerAccount, name=key, kind=kind, system_key=key)
