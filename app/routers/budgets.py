from datetime import date

from fastapi import APIRouter, Query
from sqlalchemy import select

from app import models as m
from app import schemas as s
from app.deps import IdempotencyKey, ReadContext, WriteContext
from app.services.common import atomic, public
from app.services.planning import budget_view, upsert_budget

router = APIRouter(prefix="/budgets", tags=["budgets"])


@router.get("")
async def list_budgets(
    ctx: ReadContext,
    month: date,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    rows = await ctx.db.scalars(
        select(m.Budget)
        .where(m.Budget.family_id == ctx.family_id, m.Budget.month == month)
        .order_by(m.Budget.id)
        .limit(limit)
        .offset(offset)
    )
    return public([await budget_view(ctx, r) for r in rows])


@router.put("")
async def put(payload: s.BudgetCreate, ctx: WriteContext, key: IdempotencyKey):
    return await atomic(ctx, key, "budget.upsert", payload, lambda: upsert_budget(ctx, payload))
