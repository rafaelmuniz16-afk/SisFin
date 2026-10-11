from datetime import date
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import or_, select

from app import models as m
from app import responses as out
from app import schemas as s
from app.deps import IdempotencyKey, ReadContext, WriteContext
from app.domain import NodeKind, TransactionKind, TransactionStatus
from app.services import finance, planning
from app.services.common import atomic, get, public

router = APIRouter(prefix="/transactions", tags=["transactions"])


@router.get("")
async def list_transactions(
    ctx: ReadContext,
    start: date | None = None,
    end: date | None = None,
    kind: TransactionKind | None = None,
    status: TransactionStatus | None = None,
    account_id: UUID | None = None,
    card_id: UUID | None = None,
    category_id: UUID | None = None,
    tag: str | None = None,
    q: str | None = Query(None, max_length=160),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    query = select(m.Transaction).where(
        m.Transaction.family_id == ctx.family_id, m.Transaction.node == NodeKind.ROOT
    )
    for value, column in [
        (kind, m.Transaction.kind),
        (status, m.Transaction.status),
        (account_id, m.Transaction.account_id),
        (card_id, m.Transaction.card_id),
    ]:
        if value is not None:
            query = query.where(column == value)
    if category_id:
        split_parents = select(m.Transaction.parent_id).where(
            m.Transaction.family_id == ctx.family_id,
            m.Transaction.node == NodeKind.SPLIT,
            m.Transaction.category_id == category_id,
        )
        query = query.where(
            or_(m.Transaction.category_id == category_id, m.Transaction.id.in_(split_parents))
        )
    if start:
        query = query.where(m.Transaction.competency_date >= start)
    if end:
        query = query.where(m.Transaction.competency_date <= end)
    if tag:
        query = query.where(m.Transaction.tags.contains([tag]))
    if q:
        query = query.where(
            or_(
                m.Transaction.description.icontains(q, autoescape=True),
                m.Transaction.merchant.icontains(q, autoescape=True),
            )
        )
    return public(
        list(
            await ctx.db.scalars(
                query.order_by(m.Transaction.competency_date.desc(), m.Transaction.id)
                .limit(limit)
                .offset(offset)
            )
        )
    )


@router.post("", status_code=201, response_model=out.TransactionView)
async def create(payload: s.CashTransaction, ctx: WriteContext, key: IdempotencyKey):
    return await atomic(
        ctx, key, "transaction.create", payload, lambda: finance.cash_transaction(ctx, payload)
    )


@router.post("/transfers", status_code=201, response_model=out.TransactionView)
async def transfer(payload: s.Transfer, ctx: WriteContext, key: IdempotencyKey):
    return await atomic(
        ctx, key, "transaction.transfer", payload, lambda: finance.transfer(ctx, payload)
    )


@router.get("/{transaction_id}")
async def detail(transaction_id: UUID, ctx: ReadContext):
    tx = await get(ctx, m.Transaction, transaction_id)
    children = list(
        await ctx.db.scalars(
            select(m.Transaction)
            .where(m.Transaction.family_id == ctx.family_id, m.Transaction.parent_id == tx.id)
            .order_by(m.Transaction.installment_number, m.Transaction.id)
        )
    )
    related = list(
        await ctx.db.scalars(
            select(m.Transaction).where(
                m.Transaction.family_id == ctx.family_id,
                m.Transaction.related_transaction_id == tx.id,
            )
        )
    )
    receipts = list(
        await ctx.db.scalars(
            select(m.Attachment).where(
                m.Attachment.family_id == ctx.family_id, m.Attachment.transaction_id == tx.id
            )
        )
    )
    shares = list(
        await ctx.db.scalars(
            select(m.TransactionShare).where(
                m.TransactionShare.family_id == ctx.family_id,
                m.TransactionShare.transaction_id == tx.id,
            )
        )
    )
    result = {
        **public(tx),
        "children": children,
        "related": related,
        "attachments": receipts,
        "shares": shares,
    }
    if tx.card_id and tx.node == NodeKind.INSTALLMENT:
        inv = await get(ctx, m.Invoice, tx.invoice_id)
        result["settlement_status"] = (await finance.invoice_view(ctx, inv))["status"]
    elif (
        tx.kind in (TransactionKind.INCOME, TransactionKind.EXPENSE)
        and not tx.card_id
        and tx.node == NodeKind.ROOT
    ):
        result["settled_amount"] = await finance.cash_settled(ctx, tx.id)
        result["outstanding"] = tx.amount - result["settled_amount"]
    return public(result)


@router.post("/{transaction_id}/settlements", status_code=201)
async def settle(transaction_id: UUID, payload: s.Payment, ctx: WriteContext, key: IdempotencyKey):
    return await atomic(
        ctx,
        key,
        f"transaction.settle:{transaction_id}",
        payload,
        lambda: finance.settle_cash(ctx, transaction_id, payload),
    )


@router.post("/{transaction_id}/refunds", status_code=201)
async def refund(transaction_id: UUID, payload: s.Refund, ctx: WriteContext, key: IdempotencyKey):
    return await atomic(
        ctx,
        key,
        f"transaction.refund:{transaction_id}",
        payload,
        lambda: finance.refund(ctx, transaction_id, payload),
    )


@router.post("/{transaction_id}/confirm")
async def confirm(transaction_id: UUID, payload: s.Confirm, ctx: WriteContext, key: IdempotencyKey):
    return await atomic(
        ctx,
        key,
        f"transaction.confirm:{transaction_id}",
        payload,
        lambda: planning.confirm(ctx, transaction_id, payload),
    )


@router.post("/{transaction_id}/cancel")
async def cancel(transaction_id: UUID, ctx: WriteContext, key: IdempotencyKey):
    async def action():
        tx = await get(ctx, m.Transaction, transaction_id, lock=True)
        if tx.status != TransactionStatus.PLANNED:
            raise HTTPException(
                409,
                "Somente previsões podem ser canceladas. Eventos realizados exigem compensação.",
            )
        tx.status = TransactionStatus.CANCELLED
        await ctx.db.flush()
        return tx

    return await atomic(ctx, key, f"transaction.cancel:{transaction_id}", {}, action)
