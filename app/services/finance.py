from datetime import timedelta
from decimal import Decimal

from fastapi import HTTPException
from sqlalchemy import func, select

from app import models as m
from app import schemas as s
from app.deps import Context
from app.domain import (
    ZERO,
    LedgerKind,
    NodeKind,
    PaymentMethod,
    TransactionKind,
    TransactionStatus,
    add_months,
    cycle_for_month,
    installments,
    invoice_cycle,
    invoice_status,
)
from app.services.common import (
    actual_date,
    add,
    balance,
    ensure_funds,
    get,
    journal,
    public,
    system_account,
)


async def transaction(
    ctx, *, kind, amount, description, date, payment_method=PaymentMethod.OTHER, **values
):
    return await add(
        ctx,
        m.Transaction,
        kind=kind,
        amount=amount,
        description=description,
        competency_date=date,
        purchase_date=values.pop("purchase_date", date),
        payment_method=payment_method,
        created_by=ctx.actor_id,
        **values,
    )


async def category(ctx, identifier, kind):
    cat = await get(ctx, m.Category, identifier)
    if cat.kind != kind:
        raise HTTPException(422, "Categoria incompatível com o tipo da transação.")
    return cat


async def validate_expense(ctx, payload, kind):
    actual_date(ctx, payload.competency_date)
    actual_date(ctx, payload.purchase_date)
    allocations = (
        [(p.category_id, p.amount) for p in payload.splits]
        if payload.splits
        else [(payload.category_id, payload.amount)]
    )
    for cat_id, _ in allocations:
        await category(ctx, cat_id, kind)
    for share in payload.shares:
        if not await ctx.db.get(m.FamilyMember, (ctx.family_id, share.user_id)):
            raise HTTPException(422, "Participante não pertence à família.")
    return allocations


async def decorate(ctx, tx, payload):
    for part in payload.splits:
        await transaction(
            ctx,
            kind=tx.kind,
            node=NodeKind.SPLIT,
            parent_id=tx.id,
            amount=part.amount,
            description=tx.description,
            date=tx.competency_date,
            category_id=part.category_id,
            payment_method=tx.payment_method,
        )
    for share in payload.shares:
        await add(
            ctx,
            m.TransactionShare,
            transaction_id=tx.id,
            user_id=share.user_id,
            amount=share.amount,
        )
    for receipt in payload.attachments:
        await add(ctx, m.Attachment, transaction_id=tx.id, name=receipt.name, url=str(receipt.url))


async def create_account(ctx, payload: s.AccountCreate):
    actual_date(ctx, payload.opening_date)
    owner = payload.owner_id or ctx.actor_id
    if not await ctx.db.get(m.FamilyMember, (ctx.family_id, owner)):
        raise HTTPException(422, "Titular fora da família.")
    if payload.opening_balance < 0 and not payload.allow_overdraft:
        raise HTTPException(422, "Saldo negativo exige cheque especial habilitado.")
    ledger = await add(ctx, m.LedgerAccount, name=payload.name, kind=LedgerKind.ASSET)
    acc = await add(
        ctx,
        m.Account,
        name=payload.name,
        kind=payload.kind,
        bank=payload.bank,
        ledger_account_id=ledger.id,
        owner_id=owner,
        allow_overdraft=payload.allow_overdraft,
    )
    if payload.opening_balance:
        await adjustment(
            ctx,
            acc.id,
            s.Adjustment(
                delta=payload.opening_balance, date=payload.opening_date, reason="Saldo de abertura"
            ),
        )
    return {**public(acc), "balance": await balance(ctx, ledger.id)}


async def create_card(ctx, payload: s.CardCreate):
    await get(ctx, m.Account, payload.payment_account_id)
    owner = payload.owner_id or ctx.actor_id
    if not await ctx.db.get(m.FamilyMember, (ctx.family_id, owner)):
        raise HTTPException(422, "Titular fora da família.")
    ledger = await add(ctx, m.LedgerAccount, name=payload.name, kind=LedgerKind.LIABILITY)
    card = await add(
        ctx,
        m.CreditCard,
        **payload.model_dump(exclude={"owner_id"}),
        owner_id=owner,
        ledger_account_id=ledger.id,
    )
    return await card_view(ctx, card)


async def card_view(ctx, card):
    used = -await balance(ctx, card.ledger_account_id)
    _, close, _ = invoice_cycle(ctx.today, card.closing_day, card.due_day)
    return {
        **public(card),
        "used_limit": max(used, ZERO),
        "credit_balance": max(-used, ZERO),
        "available_limit": card.credit_limit - used,
        "next_best_purchase_date": close + timedelta(days=1),
    }


async def ensure_invoice(ctx, card, cycle):
    invoice = await ctx.db.scalar(
        select(m.Invoice).where(
            m.Invoice.family_id == ctx.family_id,
            m.Invoice.card_id == card.id,
            m.Invoice.cycle_month == cycle,
        )
    )
    if invoice is None:
        _, closing, due = cycle_for_month(cycle, card.closing_day, card.due_day)
        invoice = await add(
            ctx, m.Invoice, card_id=card.id, cycle_month=cycle, closing_date=closing, due_date=due
        )
    return invoice


async def invoice_totals(ctx, invoice):
    total = Decimal(
        await ctx.db.scalar(
            select(func.coalesce(func.sum(m.InvoiceItem.amount), 0)).where(
                m.InvoiceItem.family_id == ctx.family_id, m.InvoiceItem.invoice_id == invoice.id
            )
        )
    )
    paid = Decimal(
        await ctx.db.scalar(
            select(func.coalesce(func.sum(m.InvoicePayment.amount), 0)).where(
                m.InvoicePayment.family_id == ctx.family_id,
                m.InvoicePayment.invoice_id == invoice.id,
            )
        )
    )
    return total, paid


async def invoice_view(ctx, invoice):
    total, paid = await invoice_totals(ctx, invoice)
    return {
        **public(invoice),
        "total": total,
        "paid": paid,
        "outstanding": max(total - paid, ZERO),
        "credit_balance": max(paid - total, ZERO),
        "status": invoice_status(total, paid, invoice.closing_date, invoice.due_date, ctx.today),
    }


async def credit_purchase(ctx: Context, card_id, payload: s.CreditPurchase, *, existing=None):
    allocations = await validate_expense(ctx, payload, "expense")
    billing = payload.billing_date or payload.purchase_date
    actual_date(ctx, billing)
    if billing < payload.purchase_date:
        raise HTTPException(422, "Lançamento da operadora não pode preceder a compra.")
    try:
        portions = installments(payload.amount, payload.installments)
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    card = await get(ctx, m.CreditCard, card_id, lock=True)
    used = -await balance(ctx, card.ledger_account_id)
    if not card.active or used + payload.amount > card.credit_limit:
        raise HTTPException(
            409, "Cartão inativo ou limite insuficiente para o valor INTEGRAL da compra."
        )
    if existing:
        tx = existing
        tx.amount, tx.status = payload.amount, TransactionStatus.POSTED
        tx.billing_date = billing
        tx.installment_count = payload.installments
        await ctx.db.flush()
    else:
        tx = await transaction(
            ctx,
            kind=TransactionKind.EXPENSE,
            amount=payload.amount,
            description=payload.description,
            date=payload.competency_date,
            purchase_date=payload.purchase_date,
            billing_date=billing,
            card_id=card.id,
            installment_count=payload.installments,
            category_id=payload.category_id,
            merchant=payload.merchant,
            tags=payload.tags,
            payment_method=PaymentMethod.CREDIT,
        )
    first_cycle, _, _ = invoice_cycle(billing, card.closing_day, card.due_day)
    children = []
    for number, amount in enumerate(portions, 1):
        inv = await ensure_invoice(ctx, card, add_months(first_cycle, number - 1))
        child = await transaction(
            ctx,
            kind=TransactionKind.EXPENSE,
            node=NodeKind.INSTALLMENT,
            parent_id=tx.id,
            amount=amount,
            description=payload.description,
            date=payload.competency_date,
            purchase_date=payload.purchase_date,
            card_id=card.id,
            invoice_id=inv.id,
            due_date=inv.due_date,
            installment_number=number,
            installment_count=payload.installments,
            payment_method=PaymentMethod.CREDIT,
        )
        await add(ctx, m.InvoiceItem, invoice_id=inv.id, transaction_id=child.id, amount=amount)
        # A data de quitação é da fatura inteira; novas cobranças reabrem o saldo.
        if (await invoice_view(ctx, inv))["outstanding"] > ZERO:
            for previous in await ctx.db.scalars(
                select(m.Transaction).where(
                    m.Transaction.family_id == ctx.family_id,
                    m.Transaction.invoice_id == inv.id,
                    m.Transaction.node == NodeKind.INSTALLMENT,
                    m.Transaction.paid_date.is_not(None),
                )
            ):
                previous.paid_date = None
            await ctx.db.flush()
        children.append(child)
    await decorate(ctx, tx, payload)
    expense = await system_account(ctx, "expense")
    await journal(
        ctx,
        tx,
        "recognition",
        payload.competency_date,
        payload.competency_date,
        [(expense.id, amount, ZERO, cat) for cat, amount in allocations]
        + [(card.ledger_account_id, ZERO, payload.amount, None)],
    )
    return {"purchase": tx, "installments": children, "card": await card_view(ctx, card)}


async def pay_invoice(ctx: Context, invoice_id, payload: s.Payment):
    actual_date(ctx, payload.payment_date)
    inv = await get(ctx, m.Invoice, invoice_id, lock=True)
    card = await get(ctx, m.CreditCard, inv.card_id, lock=True)
    account = await get(ctx, m.Account, payload.account_id, lock=True)
    total, paid = await invoice_totals(ctx, inv)
    last_event = await ctx.db.scalar(
        select(func.max(m.Transaction.competency_date))
        .join(m.InvoiceItem, m.InvoiceItem.transaction_id == m.Transaction.id)
        .where(m.InvoiceItem.family_id == ctx.family_id, m.InvoiceItem.invoice_id == inv.id)
    )
    if last_event and payload.payment_date < last_event:
        raise HTTPException(422, "Pagamento não pode preceder os lançamentos da fatura.")
    if payload.amount > total - paid:
        raise HTTPException(409, "Pagamento excede o saldo devedor da fatura.")
    await ensure_funds(ctx, account, payload.amount, payload.payment_date)
    tx = await transaction(
        ctx,
        kind=TransactionKind.TRANSFER,
        amount=payload.amount,
        description="Liquidação de fatura",
        date=payload.payment_date,
        payment_method=payload.payment_method,
        account_id=account.id,
        card_id=card.id,
        invoice_id=inv.id,
        paid_date=payload.payment_date,
        details={"purpose": "invoice_payment"},
    )
    await add(
        ctx,
        m.InvoicePayment,
        invoice_id=inv.id,
        transaction_id=tx.id,
        amount=payload.amount,
        payment_date=payload.payment_date,
    )
    await journal(
        ctx,
        tx,
        "invoice_payment",
        payload.payment_date,
        payload.payment_date,
        [
            (card.ledger_account_id, payload.amount, ZERO, None),
            (account.ledger_account_id, ZERO, payload.amount, None),
        ],
    )
    # Parcela é quitada quando a fatura está integralmente liquidada, sem rateio
    # arbitrário de pagamentos parciais entre estabelecimentos.
    if total == paid + payload.amount:
        last_paid = await ctx.db.scalar(
            select(func.max(m.InvoicePayment.payment_date)).where(
                m.InvoicePayment.family_id == ctx.family_id, m.InvoicePayment.invoice_id == inv.id
            )
        )
        for part in await ctx.db.scalars(
            select(m.Transaction).where(
                m.Transaction.family_id == ctx.family_id,
                m.Transaction.invoice_id == inv.id,
                m.Transaction.node == NodeKind.INSTALLMENT,
            )
        ):
            part.paid_date = last_paid
        await ctx.db.flush()
    return {
        "payment": tx,
        "invoice": await invoice_view(ctx, inv),
        "card": await card_view(ctx, card),
        "account_balance": await balance(ctx, account.ledger_account_id),
    }


async def cash_transaction(ctx, payload: s.CashTransaction, *, existing=None):
    allocations = await validate_expense(ctx, payload, payload.kind)
    account = await get(ctx, m.Account, payload.account_id, lock=True)
    if not account.active:
        raise HTTPException(409, "Conta inativa.")
    if existing:
        tx = existing
        tx.amount, tx.status = payload.amount, TransactionStatus.POSTED
        await ctx.db.flush()
    else:
        tx = await transaction(
            ctx,
            kind=TransactionKind(payload.kind),
            amount=payload.amount,
            description=payload.description,
            date=payload.competency_date,
            purchase_date=payload.purchase_date,
            due_date=payload.due_date,
            account_id=account.id,
            category_id=payload.category_id,
            merchant=payload.merchant,
            tags=payload.tags,
            payment_method=payload.payment_method,
        )
    await decorate(ctx, tx, payload)
    income = payload.kind == "income"
    counterpart = await system_account(ctx, "income" if income else "expense")
    obligation = await system_account(ctx, "receivable" if income else "payable")
    lines = [
        (counterpart.id, ZERO if income else amount, amount if income else ZERO, cat)
        for cat, amount in allocations
    ]
    lines.append(
        (
            obligation.id,
            payload.amount if income else ZERO,
            ZERO if income else payload.amount,
            None,
        )
    )
    await journal(ctx, tx, "recognition", payload.competency_date, payload.competency_date, lines)
    if payload.payment_date:
        await settle_cash(
            ctx,
            tx.id,
            s.Payment(
                account_id=account.id,
                amount=payload.amount,
                payment_date=payload.payment_date,
                payment_method=payload.payment_method,
            ),
        )
    return tx


async def cash_settled(ctx, transaction_id):
    return Decimal(
        await ctx.db.scalar(
            select(func.coalesce(func.sum(m.CashSettlement.amount), 0)).where(
                m.CashSettlement.family_id == ctx.family_id,
                m.CashSettlement.original_transaction_id == transaction_id,
            )
        )
    )


async def settle_cash(ctx, transaction_id, payload: s.Payment):
    actual_date(ctx, payload.payment_date)
    original = await get(ctx, m.Transaction, transaction_id, lock=True)
    if (
        original.card_id
        or original.node != NodeKind.ROOT
        or original.kind not in (TransactionKind.EXPENSE, TransactionKind.INCOME)
        or original.status != TransactionStatus.POSTED
    ):
        raise HTTPException(409, "Transação incompatível com liquidação em conta.")
    if payload.payment_date < original.competency_date:
        raise HTTPException(422, "Adiantamentos anteriores à competência exigem módulo próprio.")
    paid = await cash_settled(ctx, original.id)
    if payload.amount > original.amount - paid:
        raise HTTPException(409, "Liquidação excede obrigação.")
    account = await get(ctx, m.Account, payload.account_id, lock=True)
    income = original.kind == TransactionKind.INCOME
    if not income:
        await ensure_funds(ctx, account, payload.amount, payload.payment_date)
    elif not account.active:
        raise HTTPException(409, "Conta inativa.")
    tx = await transaction(
        ctx,
        kind=TransactionKind.TRANSFER,
        amount=payload.amount,
        description="Liquidação: " + original.description[:225],
        date=payload.payment_date,
        paid_date=payload.payment_date,
        account_id=account.id,
        payment_method=payload.payment_method,
        related_transaction_id=original.id,
        details={"purpose": "cash_settlement"},
    )
    await add(
        ctx,
        m.CashSettlement,
        original_transaction_id=original.id,
        settlement_transaction_id=tx.id,
        amount=payload.amount,
        payment_date=payload.payment_date,
    )
    obligation = await system_account(ctx, "receivable" if income else "payable")
    await journal(
        ctx,
        tx,
        "settlement",
        payload.payment_date,
        payload.payment_date,
        [
            (
                account.ledger_account_id,
                payload.amount if income else ZERO,
                ZERO if income else payload.amount,
                None,
            ),
            (
                obligation.id,
                ZERO if income else payload.amount,
                payload.amount if income else ZERO,
                None,
            ),
        ],
    )
    if paid + payload.amount == original.amount:
        original.paid_date = await ctx.db.scalar(
            select(func.max(m.CashSettlement.payment_date)).where(
                m.CashSettlement.original_transaction_id == original.id,
                m.CashSettlement.family_id == ctx.family_id,
            )
        )
        await ctx.db.flush()
    return tx


async def transfer(ctx, payload: s.Transfer):
    actual_date(ctx, payload.date)
    accounts = {}
    for identifier in sorted([payload.source_account_id, payload.destination_account_id], key=str):
        accounts[identifier] = await get(ctx, m.Account, identifier, lock=True)
    source, destination = (
        accounts[payload.source_account_id],
        accounts[payload.destination_account_id],
    )
    await ensure_funds(ctx, source, payload.amount, payload.date)
    if not destination.active:
        raise HTTPException(409, "Conta de destino inativa.")
    tx = await transaction(
        ctx,
        kind=TransactionKind.TRANSFER,
        amount=payload.amount,
        date=payload.date,
        paid_date=payload.date,
        description=payload.description,
        account_id=source.id,
        destination_account_id=destination.id,
        payment_method=PaymentMethod.BANK_TRANSFER,
    )
    await journal(
        ctx,
        tx,
        "transfer",
        payload.date,
        payload.date,
        [
            (destination.ledger_account_id, payload.amount, ZERO, None),
            (source.ledger_account_id, ZERO, payload.amount, None),
        ],
    )
    return tx


async def adjustment(ctx, account_id, payload: s.Adjustment):
    actual_date(ctx, payload.date)
    account = await get(ctx, m.Account, account_id, lock=True)
    if payload.delta < 0:
        await ensure_funds(ctx, account, -payload.delta, payload.date)
    equity = await system_account(ctx, "equity")
    tx = await transaction(
        ctx,
        kind=TransactionKind.ADJUSTMENT,
        amount=abs(payload.delta),
        date=payload.date,
        paid_date=payload.date,
        description=payload.reason,
        account_id=account.id,
        details={"delta": str(payload.delta)},
    )
    positive = payload.delta > 0
    await journal(
        ctx,
        tx,
        "adjustment",
        payload.date,
        payload.date,
        [
            (
                account.ledger_account_id,
                abs(payload.delta) if positive else ZERO,
                ZERO if positive else abs(payload.delta),
                None,
            ),
            (
                equity.id,
                ZERO if positive else abs(payload.delta),
                abs(payload.delta) if positive else ZERO,
                None,
            ),
        ],
    )
    return tx


async def refund(ctx, original_id, payload: s.Refund):
    actual_date(ctx, payload.date)
    original = await get(ctx, m.Transaction, original_id, lock=True)
    if (
        original.kind != TransactionKind.EXPENSE
        or original.node != NodeKind.ROOT
        or original.status != TransactionStatus.POSTED
    ):
        raise HTTPException(409, "Vínculo deve ser uma despesa original confirmada.")
    if payload.date < original.competency_date:
        raise HTTPException(422, "Devolução não pode preceder a competência original.")
    await category(ctx, payload.category_id, "expense")
    expense = await system_account(ctx, "expense")
    net_category = Decimal(
        await ctx.db.scalar(
            select(func.coalesce(func.sum(m.JournalLine.debit - m.JournalLine.credit), 0))
            .join(m.Journal, m.Journal.id == m.JournalLine.journal_id)
            .join(m.Transaction, m.Transaction.id == m.Journal.transaction_id)
            .where(
                m.JournalLine.family_id == ctx.family_id,
                m.JournalLine.category_id == payload.category_id,
                m.JournalLine.ledger_account_id == expense.id,
                (m.Transaction.id == original.id)
                | (m.Transaction.related_transaction_id == original.id),
            )
        )
    )
    if payload.amount > net_category:
        raise HTTPException(
            409, "Devoluções acumuladas excedem o gasto líquido desta categoria na compra."
        )
    # Para despesa em conta, só devolvemos desembolso já liquidado. Evita criar
    # caixa a partir de uma obrigação não paga.
    if not original.card_id:
        refunded = Decimal(
            await ctx.db.scalar(
                select(func.coalesce(func.sum(m.Transaction.amount), 0)).where(
                    m.Transaction.family_id == ctx.family_id,
                    m.Transaction.related_transaction_id == original.id,
                    m.Transaction.kind.in_(
                        [TransactionKind.REIMBURSEMENT, TransactionKind.REVERSAL]
                    ),
                )
            )
        )
        if refunded + payload.amount > await cash_settled(ctx, original.id):
            raise HTTPException(409, "Devolução excede o desembolso liquidado.")
    if payload.invoice_id:
        inv = await get(ctx, m.Invoice, payload.invoice_id, lock=True)
        if inv.card_id != original.card_id:
            raise HTTPException(422, "Fatura deve pertencer ao cartão da compra.")
        card = await get(ctx, m.CreditCard, inv.card_id, lock=True)
        destination_id = card.ledger_account_id
    else:
        account = await get(ctx, m.Account, payload.account_id, lock=True)
        if not account.active:
            raise HTTPException(409, "Conta inativa.")
        destination_id = account.ledger_account_id
    tx = await transaction(
        ctx,
        kind=TransactionKind(payload.kind),
        amount=payload.amount,
        description=payload.description,
        date=payload.date,
        paid_date=payload.date,
        related_transaction_id=original.id,
        category_id=payload.category_id,
        invoice_id=payload.invoice_id,
        account_id=payload.account_id,
        card_id=original.card_id if payload.invoice_id else None,
        payment_method=PaymentMethod.CREDIT if payload.invoice_id else PaymentMethod.OTHER,
    )
    if payload.invoice_id:
        await add(
            ctx,
            m.InvoiceItem,
            invoice_id=payload.invoice_id,
            transaction_id=tx.id,
            amount=-payload.amount,
        )
    await journal(
        ctx,
        tx,
        "refund",
        payload.date,
        payload.date,
        [
            (destination_id, payload.amount, ZERO, None),
            (expense.id, ZERO, payload.amount, payload.category_id),
        ],
    )
    return tx


async def transfer_invoice_credit(ctx, source_id, payload: s.CreditTransfer):
    actual_date(ctx, payload.date)
    source = await get(ctx, m.Invoice, source_id, lock=True)
    dest = await get(ctx, m.Invoice, payload.destination_invoice_id, lock=True)
    if source.id == dest.id or source.card_id != dest.card_id:
        raise HTTPException(422, "Escolha outra fatura do mesmo cartão.")
    total, paid = await invoice_totals(ctx, source)
    if payload.amount > paid - total:
        raise HTTPException(409, "Crédito de fatura insuficiente.")
    tx = await transaction(
        ctx,
        kind=TransactionKind.TRANSFER,
        amount=payload.amount,
        description="Realocação de crédito entre faturas",
        date=payload.date,
        card_id=source.card_id,
        payment_method=PaymentMethod.CREDIT,
        details={"source_invoice_id": str(source.id), "destination_invoice_id": str(dest.id)},
    )
    await add(ctx, m.InvoiceItem, invoice_id=source.id, transaction_id=tx.id, amount=payload.amount)
    await add(ctx, m.InvoiceItem, invoice_id=dest.id, transaction_id=tx.id, amount=-payload.amount)
    # Mesma conta contábil: somente realoca o auxiliar, sem novo lançamento no razão.
    return {
        "transaction": tx,
        "source": await invoice_view(ctx, source),
        "destination": await invoice_view(ctx, dest),
    }
