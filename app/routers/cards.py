from uuid import UUID

from fastapi import APIRouter, Query
from sqlalchemy import select

from app import models as m
from app import responses as out
from app import schemas as s
from app.deps import IdempotencyKey, ReadContext, WriteContext
from app.services import finance
from app.services.common import atomic, get, public

router = APIRouter(prefix="/cards", tags=["cards"])


@router.get("", response_model=list[out.CardView])
async def list_cards(
    ctx: ReadContext, limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0)
):
    rows = await ctx.db.scalars(
        select(m.CreditCard)
        .where(m.CreditCard.family_id == ctx.family_id)
        .order_by(m.CreditCard.id)
        .limit(limit)
        .offset(offset)
    )
    return public([await finance.card_view(ctx, r) for r in rows])


@router.post("", status_code=201, response_model=out.CardView)
async def create(payload: s.CardCreate, ctx: WriteContext, key: IdempotencyKey):
    return await atomic(ctx, key, "card.create", payload, lambda: finance.create_card(ctx, payload))


@router.get("/{card_id}", response_model=out.CardView)
async def detail(card_id: UUID, ctx: ReadContext):
    return public(await finance.card_view(ctx, await get(ctx, m.CreditCard, card_id)))


@router.post("/{card_id}/purchases", status_code=201, response_model=out.PurchaseView)
async def purchase(
    card_id: UUID, payload: s.CreditPurchase, ctx: WriteContext, key: IdempotencyKey
):
    return await atomic(
        ctx,
        key,
        f"card.purchase:{card_id}",
        payload,
        lambda: finance.credit_purchase(ctx, card_id, payload),
    )
