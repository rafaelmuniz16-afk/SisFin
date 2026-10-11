from uuid import UUID

from fastapi import APIRouter, Query
from sqlalchemy import select

from app import models as m
from app import responses as out
from app import schemas as s
from app.deps import IdempotencyKey, ReadContext, WriteContext
from app.services import finance
from app.services.common import atomic, balance, get, public

router = APIRouter(prefix="/accounts", tags=["accounts"])


@router.get("", response_model=list[out.AccountView])
async def list_accounts(
    ctx: ReadContext, limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0)
):
    rows = await ctx.db.scalars(
        select(m.Account)
        .where(m.Account.family_id == ctx.family_id)
        .order_by(m.Account.id)
        .limit(limit)
        .offset(offset)
    )
    return public([{**public(r), "balance": await balance(ctx, r.ledger_account_id)} for r in rows])


@router.post("", status_code=201, response_model=out.AccountView)
async def create(payload: s.AccountCreate, ctx: WriteContext, key: IdempotencyKey):
    return await atomic(
        ctx, key, "account.create", payload, lambda: finance.create_account(ctx, payload)
    )


@router.get("/{account_id}", response_model=out.AccountView)
async def detail(account_id: UUID, ctx: ReadContext):
    row = await get(ctx, m.Account, account_id)
    return public({**public(row), "balance": await balance(ctx, row.ledger_account_id)})


@router.post("/{account_id}/adjustments", status_code=201)
async def adjustment(
    account_id: UUID, payload: s.Adjustment, ctx: WriteContext, key: IdempotencyKey
):
    return await atomic(
        ctx,
        key,
        f"account.adjust:{account_id}",
        payload,
        lambda: finance.adjustment(ctx, account_id, payload),
    )
