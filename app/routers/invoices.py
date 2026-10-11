from uuid import UUID

from fastapi import APIRouter, Query
from sqlalchemy import select

from app import models as m
from app import responses as out
from app import schemas as s
from app.deps import IdempotencyKey, ReadContext, WriteContext
from app.services import finance
from app.services.common import atomic, get, public

router = APIRouter(prefix="/invoices", tags=["invoices"])


@router.get("", response_model=list[out.InvoiceView])
async def list_invoices(
    ctx: ReadContext,
    card_id: UUID | None = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    query = select(m.Invoice).where(m.Invoice.family_id == ctx.family_id)
    if card_id:
        query = query.where(m.Invoice.card_id == card_id)
    rows = await ctx.db.scalars(
        query.order_by(m.Invoice.due_date, m.Invoice.id).limit(limit).offset(offset)
    )
    return public([await finance.invoice_view(ctx, r) for r in rows])


@router.get("/{invoice_id}")
async def detail(invoice_id: UUID, ctx: ReadContext):
    row = await get(ctx, m.Invoice, invoice_id)
    items = list(
        await ctx.db.scalars(
            select(m.InvoiceItem).where(
                m.InvoiceItem.family_id == ctx.family_id, m.InvoiceItem.invoice_id == row.id
            )
        )
    )
    return public({**await finance.invoice_view(ctx, row), "items": items})


@router.post("/{invoice_id}/payments", status_code=201, response_model=out.InvoicePaymentView)
async def payment(invoice_id: UUID, payload: s.Payment, ctx: WriteContext, key: IdempotencyKey):
    return await atomic(
        ctx,
        key,
        f"invoice.pay:{invoice_id}",
        payload,
        lambda: finance.pay_invoice(ctx, invoice_id, payload),
    )


@router.post("/{invoice_id}/credit-transfers", status_code=201)
async def move_credit(
    invoice_id: UUID, payload: s.CreditTransfer, ctx: WriteContext, key: IdempotencyKey
):
    return await atomic(
        ctx,
        key,
        f"invoice.credit:{invoice_id}",
        payload,
        lambda: finance.transfer_invoice_credit(ctx, invoice_id, payload),
    )
