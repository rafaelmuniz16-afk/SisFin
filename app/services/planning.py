from collections import defaultdict
from datetime import timedelta
from decimal import ROUND_CEILING, Decimal

from fastapi import HTTPException
from sqlalchemy import func, select

from app import models as m
from app import schemas as s
from app.domain import (
    ZERO,
    LedgerKind,
    NodeKind,
    TransactionKind,
    TransactionStatus,
    add_months,
    invoice_cycle,
    occurrences,
)
from app.services.common import actual_date, add, balance, get, journal, public, system_account
from app.services.finance import (
    cash_settled,
    cash_transaction,
    category,
    credit_purchase,
    invoice_totals,
    transaction,
)


async def create_recurrence(ctx, payload: s.RecurrenceCreate):
    await category(ctx, payload.category_id, payload.kind)
    if payload.card_id:
        await get(ctx, m.CreditCard, payload.card_id)
    else:
        await get(ctx, m.Account, payload.account_id)
    return await add(ctx, m.Recurrence, **payload.model_dump())


def recurrence_dates(rec, start, end):
    return occurrences(rec.start_date, rec.end_date, rec.frequency, rec.interval, start, end)


async def materialize(ctx, recurrence_id, payload: s.DateRange):
    rec = await get(ctx, m.Recurrence, recurrence_id, lock=True)
    if not rec.active:
        raise HTTPException(409, "Recorrência inativa.")
    created = []
    for day in recurrence_dates(rec, payload.start, payload.end):
        existing = await ctx.db.scalar(
            select(m.Transaction).where(
                m.Transaction.family_id == ctx.family_id,
                m.Transaction.recurrence_id == rec.id,
                m.Transaction.occurrence_date == day,
            )
        )
        if existing:
            continue
        tx = await transaction(
            ctx,
            kind=TransactionKind(rec.kind),
            amount=rec.amount,
            description=rec.name,
            date=day,
            due_date=day,
            status=TransactionStatus.PLANNED,
            account_id=rec.account_id,
            card_id=rec.card_id,
            category_id=rec.category_id,
            recurrence_id=rec.id,
            occurrence_date=day,
            payment_method=rec.payment_method,
            details={"estimated": rec.variable},
        )
        created.append(tx)
    return {"created": created}


async def confirm(ctx, transaction_id, payload: s.Confirm):
    tx = await get(ctx, m.Transaction, transaction_id, lock=True)
    if tx.status != TransactionStatus.PLANNED or tx.node != NodeKind.ROOT:
        raise HTTPException(409, "Somente previsão pendente pode ser confirmada.")
    actual_date(ctx, tx.competency_date)
    if tx.details.get("estimated") and payload.amount is None:
        raise HTTPException(422, "Confirme o valor efetivo da recorrência variável.")
    common = dict(
        amount=payload.amount or tx.amount,
        description=tx.description,
        competency_date=tx.competency_date,
        purchase_date=tx.purchase_date,
        category_id=tx.category_id,
    )
    if tx.card_id:
        if payload.payment_date:
            raise HTTPException(422, "Compra no cartão será liquidada pela fatura.")
        return await credit_purchase(ctx, tx.card_id, s.CreditPurchase(**common), existing=tx)
    return await cash_transaction(
        ctx,
        s.CashTransaction(
            **common,
            kind=tx.kind.value,
            account_id=tx.account_id,
            due_date=tx.due_date,
            payment_method=tx.payment_method,
            payment_date=payload.payment_date,
        ),
        existing=tx,
    )


async def economic_report(ctx, start, end):
    rows = (
        await ctx.db.execute(
            select(
                m.LedgerAccount.kind,
                m.JournalLine.category_id,
                func.sum(m.JournalLine.debit - m.JournalLine.credit),
            )
            .join(m.Journal, m.Journal.id == m.JournalLine.journal_id)
            .join(m.LedgerAccount, m.LedgerAccount.id == m.JournalLine.ledger_account_id)
            .where(
                m.JournalLine.family_id == ctx.family_id,
                m.Journal.economic_date.between(start, end),
                m.LedgerAccount.kind.in_([LedgerKind.INCOME, LedgerKind.EXPENSE]),
            )
            .group_by(m.LedgerAccount.kind, m.JournalLine.category_id)
        )
    ).all()
    income, expense, categories = ZERO, ZERO, []
    for kind, category_id, value in rows:
        amount = -value if kind == LedgerKind.INCOME else value
        if kind == LedgerKind.INCOME:
            income += amount
        else:
            expense += amount
        categories.append({"category_id": category_id, "kind": kind, "amount": amount})
    return {
        "start": start,
        "end": end,
        "income": income,
        "expense": expense,
        "surplus": income - expense,
        "categories": categories,
        "savings_rate_percent": ((income - expense) / income * 100).quantize(Decimal("0.01"))
        if income
        else None,
    }


async def cash_report(ctx, start, end):
    rows = (
        await ctx.db.execute(
            select(
                m.Journal.effective_date,
                m.Account.id,
                m.Transaction.kind,
                m.Transaction.description,
                m.JournalLine.debit,
                m.JournalLine.credit,
            )
            .select_from(m.JournalLine)
            .join(m.Journal, m.Journal.id == m.JournalLine.journal_id)
            .join(m.Transaction, m.Transaction.id == m.Journal.transaction_id)
            .join(m.Account, m.Account.ledger_account_id == m.JournalLine.ledger_account_id)
            .where(
                m.JournalLine.family_id == ctx.family_id,
                m.Journal.effective_date.between(start, end),
            )
            .order_by(m.Journal.effective_date, m.JournalLine.id)
        )
    ).all()
    inflow = sum((r.debit for r in rows), ZERO)
    outflow = sum((r.credit for r in rows), ZERO)
    # Entradas/saídas brutas incluem transferências. Saldo líquido consolidado as anula.
    return {
        "start": start,
        "end": end,
        "gross_inflow": inflow,
        "gross_outflow": outflow,
        "net_change": inflow - outflow,
        "entries": [dict(r._mapping) for r in rows],
    }


async def net_worth(ctx, at):
    rows = (
        await ctx.db.execute(
            select(
                m.LedgerAccount.id,
                m.LedgerAccount.name,
                m.LedgerAccount.kind,
                func.sum(m.JournalLine.debit - m.JournalLine.credit).label("value"),
            )
            .select_from(m.JournalLine)
            .join(m.Journal, m.Journal.id == m.JournalLine.journal_id)
            .join(m.LedgerAccount, m.LedgerAccount.id == m.JournalLine.ledger_account_id)
            .where(
                m.JournalLine.family_id == ctx.family_id,
                m.Journal.effective_date <= at,
                m.LedgerAccount.kind.in_([LedgerKind.ASSET, LedgerKind.LIABILITY]),
            )
            .group_by(m.LedgerAccount.id)
        )
    ).all()
    # Saldos invertidos são reclassificados: cartão credor é ativo; overdraft, passivo.
    assets = sum((max(r.value, ZERO) for r in rows), ZERO)
    liabilities = sum((max(-r.value, ZERO) for r in rows), ZERO)
    return {
        "date": at,
        "assets": assets,
        "liabilities": liabilities,
        "net_worth": assets - liabilities,
        "positions": [dict(r._mapping) for r in rows],
    }


async def budget_view(ctx, budget):
    budgets = list(
        await ctx.db.scalars(
            select(m.Budget)
            .where(
                m.Budget.family_id == ctx.family_id,
                m.Budget.category_id == budget.category_id,
                m.Budget.month <= budget.month,
            )
            .order_by(m.Budget.month)
        )
    )
    first = budgets[0].month
    rows = (
        await ctx.db.execute(
            select(
                func.date_trunc("month", m.Journal.economic_date),
                func.sum(m.JournalLine.debit - m.JournalLine.credit),
            )
            .select_from(m.JournalLine)
            .join(m.Journal, m.Journal.id == m.JournalLine.journal_id)
            .join(m.LedgerAccount, m.LedgerAccount.id == m.JournalLine.ledger_account_id)
            .where(
                m.JournalLine.family_id == ctx.family_id,
                m.JournalLine.category_id == budget.category_id,
                m.LedgerAccount.kind == LedgerKind.EXPENSE,
                m.Journal.economic_date >= first,
                m.Journal.economic_date < add_months(budget.month, 1),
            )
            .group_by(func.date_trunc("month", m.Journal.economic_date))
        )
    ).all()
    spend = {month.date(): value for month, value in rows}
    remainder, previous = ZERO, None
    for row in budgets:
        carry = (
            remainder
            if row.rollover and previous and add_months(previous, 1) == row.month
            else ZERO
        )
        available = row.amount + carry
        spent = spend.get(row.month, ZERO)
        remainder = available - spent
        previous = row.month
    return {
        **public(budget),
        "rollover_amount": carry,
        "available": available,
        "spent": spent,
        "remaining": remainder,
    }


async def upsert_budget(ctx, payload: s.BudgetCreate):
    await category(ctx, payload.category_id, "expense")
    row = await ctx.db.scalar(
        select(m.Budget)
        .where(
            m.Budget.family_id == ctx.family_id,
            m.Budget.category_id == payload.category_id,
            m.Budget.month == payload.month,
        )
        .with_for_update()
    )
    if row:
        row.amount, row.rollover = payload.amount, payload.rollover
        await ctx.db.flush()
    else:
        row = await add(ctx, m.Budget, **payload.model_dump())
    return await budget_view(ctx, row)


async def goal_view(ctx, goal):
    current = Decimal(
        await ctx.db.scalar(
            select(func.coalesce(func.sum(m.GoalContribution.amount), 0)).where(
                m.GoalContribution.family_id == ctx.family_id, m.GoalContribution.goal_id == goal.id
            )
        )
    )
    remaining = max(goal.target_amount - current, ZERO)
    months = (
        int((remaining / goal.monthly_contribution).to_integral_value(rounding=ROUND_CEILING))
        if goal.monthly_contribution
        else None
    )
    completion = add_months(ctx.today, months) if months is not None and months <= 1200 else None
    if not remaining:
        completion = ctx.today
    return {
        **public(goal),
        "current_amount": current,
        "progress_percent": (current / goal.target_amount * 100).quantize(Decimal("0.01")),
        "estimated_completion": completion,
        "on_track": completion <= goal.target_date if completion else None,
    }


async def contribute(ctx, goal_id, payload: s.Contribution):
    actual_date(ctx, payload.date)
    goal = await get(ctx, m.Goal, goal_id, lock=True)
    account = await get(ctx, m.Account, goal.account_id, lock=True)
    view = await goal_view(ctx, goal)
    if view["current_amount"] + payload.amount < 0:
        raise HTTPException(409, "Retirada excede o valor reservado na meta.")
    reserved = Decimal(
        await ctx.db.scalar(
            select(func.coalesce(func.sum(m.GoalContribution.amount), 0))
            .join(m.Goal, m.Goal.id == m.GoalContribution.goal_id)
            .where(m.GoalContribution.family_id == ctx.family_id, m.Goal.account_id == account.id)
        )
    )
    if payload.amount > 0 and reserved + payload.amount > await balance(
        ctx, account.ledger_account_id
    ):
        raise HTTPException(409, "Reservas das metas excederiam o saldo da conta.")
    await add(ctx, m.GoalContribution, goal_id=goal.id, amount=payload.amount, date=payload.date)
    return await goal_view(ctx, goal)


async def create_position(ctx, payload: s.PositionCreate):
    actual_date(ctx, payload.date)
    ledger = await add(ctx, m.LedgerAccount, name=payload.name, kind=LedgerKind(payload.kind))
    position = await add(
        ctx, m.WealthPosition, name=payload.name, kind=payload.kind, ledger_account_id=ledger.id
    )
    if payload.value:
        await value_position(
            ctx,
            position.id,
            s.Valuation(value=payload.value, date=payload.date, reason="Avaliação inicial"),
        )
    return position


async def value_position(ctx, position_id, payload: s.Valuation):
    actual_date(ctx, payload.date)
    position = await get(ctx, m.WealthPosition, position_id, lock=True)
    last = await ctx.db.scalar(
        select(func.max(m.Journal.effective_date))
        .join(m.JournalLine, m.JournalLine.journal_id == m.Journal.id)
        .where(
            m.Journal.family_id == ctx.family_id,
            m.JournalLine.ledger_account_id == position.ledger_account_id,
        )
    )
    if last and payload.date < last:
        raise HTTPException(
            409, "Avaliação deve ser posterior ou igual à última; não reescreve o histórico."
        )
    sign = 1 if position.kind == "asset" else -1
    current = await balance(ctx, position.ledger_account_id)
    delta = payload.value * sign - current
    if not delta:
        return {"position": position, "value": payload.value}
    tx = await transaction(
        ctx,
        kind=TransactionKind.ADJUSTMENT,
        amount=abs(delta),
        description=payload.reason,
        date=payload.date,
        details={"position_id": str(position.id)},
    )
    equity = await system_account(ctx, "equity")
    await journal(
        ctx,
        tx,
        "valuation",
        payload.date,
        payload.date,
        [
            (position.ledger_account_id, max(delta, ZERO), max(-delta, ZERO), None),
            (equity.id, max(-delta, ZERO), max(delta, ZERO), None),
        ],
    )
    return {"position": position, "value": payload.value}


async def forecast(ctx, through):
    today = ctx.today
    if not today <= through <= today + timedelta(days=1096):
        raise HTTPException(422, "Horizonte deve estar entre hoje e 1.096 dias.")
    accounts = list(
        await ctx.db.scalars(select(m.Account).where(m.Account.family_id == ctx.family_id))
    )
    balances = {a.id: await balance(ctx, a.ledger_account_id) for a in accounts}
    initial = sum(balances.values(), ZERO)
    events = []

    def event(day, account_id, amount, source, estimated=False):
        if day <= through:
            events.append(
                {
                    "date": max(day, today),
                    "original_date": day,
                    "account_id": account_id,
                    "amount": amount,
                    "source": source,
                    "estimated": estimated,
                    "overdue": day < today,
                }
            )

    cards = {
        c.id: c
        for c in await ctx.db.scalars(
            select(m.CreditCard).where(m.CreditCard.family_id == ctx.family_id)
        )
    }
    card_schedule = {}

    def planned_card_charge(card, day, amount, estimated):
        cycle, _, due = invoice_cycle(day, card.closing_day, card.due_day)
        key = (card.id, cycle)
        row = card_schedule.setdefault(key, {"due": due, "net": ZERO, "estimated": False})
        row["net"] += amount
        row["estimated"] = row["estimated"] or estimated

    for inv in await ctx.db.scalars(
        select(m.Invoice).where(m.Invoice.family_id == ctx.family_id, m.Invoice.due_date <= through)
    ):
        total, paid = await invoice_totals(ctx, inv)
        card_schedule[(inv.card_id, inv.cycle_month)] = {
            "due": inv.due_date,
            "net": total - paid,
            "estimated": False,
        }
    rows = list(
        await ctx.db.scalars(
            select(m.Transaction).where(
                m.Transaction.family_id == ctx.family_id,
                m.Transaction.node == NodeKind.ROOT,
                m.Transaction.kind.in_([TransactionKind.INCOME, TransactionKind.EXPENSE]),
            )
        )
    )
    # Todas as ocorrências existentes, inclusive canceladas, suprimem a previsão virtual.
    existing = {(t.recurrence_id, t.occurrence_date) for t in rows if t.recurrence_id}
    for tx in rows:
        if tx.status == TransactionStatus.CANCELLED:
            continue
        if tx.card_id:
            if tx.status == TransactionStatus.PLANNED:
                card = cards[tx.card_id]
                planned_card_charge(card, tx.purchase_date, tx.amount, True)
        elif tx.account_id:
            pending = (
                tx.amount
                if tx.status == TransactionStatus.PLANNED
                else tx.amount - await cash_settled(ctx, tx.id)
            )
            if pending > 0:
                event(
                    tx.due_date or tx.competency_date,
                    tx.account_id,
                    pending if tx.kind == TransactionKind.INCOME else -pending,
                    f"transaction:{tx.id}",
                    tx.status == TransactionStatus.PLANNED,
                )
    for rec in await ctx.db.scalars(
        select(m.Recurrence).where(
            m.Recurrence.family_id == ctx.family_id, m.Recurrence.active.is_(True)
        )
    ):
        for day in recurrence_dates(rec, today, through):
            if (rec.id, day) in existing:
                continue
            if rec.card_id:
                card = cards[rec.card_id]
                planned_card_charge(card, day, rec.amount, rec.variable)
            else:
                event(
                    day,
                    rec.account_id,
                    rec.amount if rec.kind == "income" else -rec.amount,
                    f"recurrence:{rec.id}:{day}",
                    rec.variable,
                )
    # Compensa créditos e compras previstas NO MESMO ciclo. Entre ciclos, a
    # realocação é explícita e não se presume recebimento bancário de crédito.
    for (card_id, cycle), row in card_schedule.items():
        if row["net"] > ZERO:
            event(
                row["due"],
                cards[card_id].payment_account_id,
                -row["net"],
                f"card_cycle:{card_id}:{cycle}",
                row["estimated"],
            )
    # Agrupa por dia: não inventa ordem intradiária entre salário e vencimentos.
    grouped = defaultdict(list)
    for item in events:
        grouped[item["date"]].append(item)
    days = []
    first_negative = today if any(v < 0 for v in balances.values()) else None
    for day in sorted(grouped):
        for item in grouped[day]:
            balances[item["account_id"]] += item["amount"]
        negative = [str(k) for k, value in balances.items() if value < 0]
        if negative and first_negative is None:
            first_negative = day
        days.append(
            {
                "date": day,
                "balance": sum(balances.values(), ZERO),
                "accounts": {str(k): v for k, v in balances.items()},
                "negative_accounts": negative,
                "events": grouped[day],
            }
        )
    return {
        "as_of": today,
        "through": through,
        "initial_balance": initial,
        "final_balance": sum(balances.values(), ZERO),
        "first_negative_date": first_negative,
        "days": days,
        "assumptions": [
            "Pagamento integral das faturas na conta padrão.",
            "Vencidos sem baixa são projetados para hoje.",
            "Previsão no fechamento do dia; sem juros, câmbio ou ordem intradiária.",
            "Créditos de fatura só compensam outra fatura após realocação explícita.",
        ],
    }
