"""Funções puras: nenhum valor monetário passa por float."""

from calendar import monthrange
from datetime import date, timedelta
from decimal import Decimal, DecimalException
from enum import StrEnum

CENT = Decimal("0.01")
ZERO = Decimal("0.00")


class Role(StrEnum):
    ADMIN = "admin"
    EDITOR = "editor"
    VIEWER = "viewer"


class AccountKind(StrEnum):
    CHECKING = "checking"
    SAVINGS = "savings"
    CASH = "cash"
    INVESTMENT = "investment"
    WALLET = "wallet"


class LedgerKind(StrEnum):
    ASSET = "asset"
    LIABILITY = "liability"
    EQUITY = "equity"
    INCOME = "income"
    EXPENSE = "expense"


class TransactionKind(StrEnum):
    INCOME = "income"
    EXPENSE = "expense"
    TRANSFER = "transfer"
    REIMBURSEMENT = "reimbursement"
    REVERSAL = "reversal"
    ADJUSTMENT = "adjustment"


class NodeKind(StrEnum):
    ROOT = "root"
    INSTALLMENT = "installment"
    SPLIT = "split"


class TransactionStatus(StrEnum):
    PLANNED = "planned"
    POSTED = "posted"
    CANCELLED = "cancelled"


class PaymentMethod(StrEnum):
    PIX = "pix"
    CREDIT = "credit"
    DEBIT = "debit"
    BOLETO = "boleto"
    CASH = "cash"
    BANK_TRANSFER = "bank_transfer"
    AUTOMATIC_DEBIT = "automatic_debit"
    WALLET = "wallet"
    TED = "ted"
    OTHER = "other"


class Frequency(StrEnum):
    DAILY = "daily"
    WEEKLY = "weekly"
    FORTNIGHTLY = "fortnightly"
    MONTHLY = "monthly"
    YEARLY = "yearly"


def money(value: Decimal | str | int) -> Decimal:
    if isinstance(value, (float, bool)):
        raise ValueError("Envie dinheiro como string decimal, nunca float.")
    try:
        result = Decimal(value)
        if not result.is_finite() or abs(result) > Decimal("9999999999.99"):
            raise ValueError("Valor inválido ou excede NUMERIC(12,2).")
        if result != result.quantize(CENT):
            raise ValueError("Valor deve ter no máximo duas casas decimais.")
        return result.quantize(CENT)
    except (DecimalException, TypeError) as exc:
        raise ValueError("Valor monetário inválido.") from exc


def installments(total: Decimal, count: int) -> list[Decimal]:
    total = money(total)
    if not 1 <= count <= 600 or total <= ZERO:
        raise ValueError("Valor positivo e 1 a 600 parcelas são obrigatórios.")
    cents = int(total * 100)
    if count > cents:
        raise ValueError("Cada parcela deve ter pelo menos R$ 0,01.")
    quotient, remainder = divmod(cents, count)
    return [Decimal(quotient + (i < remainder)) / 100 for i in range(count)]


def month_start(value: date) -> date:
    return value.replace(day=1)


def add_months(anchor: date, count: int) -> date:
    month_index = anchor.year * 12 + anchor.month - 1 + count
    year, month = divmod(month_index, 12)
    month += 1
    return date(year, month, min(anchor.day, monthrange(year, month)[1]))


def day_in_month(month: date, day: int) -> date:
    return month.replace(day=min(day, monthrange(month.year, month.month)[1]))


def invoice_cycle(billing_date: date, closing_day: int, due_day: int) -> tuple[date, date, date]:
    """Compras no próprio fechamento entram nesse ciclo; depois, no próximo.

    ciclo é o MÊS DE FECHAMENTO, não necessariamente o mês do vencimento.
    """
    cycle = month_start(billing_date)
    closing = day_in_month(cycle, closing_day)
    if billing_date > closing:
        cycle = add_months(cycle, 1)
        closing = day_in_month(cycle, closing_day)
    due = day_in_month(cycle, due_day)
    if due <= closing:
        due = day_in_month(add_months(cycle, 1), due_day)
    return cycle, closing, due


def cycle_for_month(cycle: date, closing_day: int, due_day: int) -> tuple[date, date, date]:
    return invoice_cycle(month_start(cycle), closing_day, due_day)


def occurrences(
    start: date, end: date | None, frequency: Frequency, interval: int, after: date, through: date
) -> list[date]:
    """Ancoragem original preserva dia 31 e 29/02, sem drift após fevereiro."""
    if interval < 1 or through < after:
        raise ValueError("Intervalo inválido.")
    limit = min(through, end) if end else through
    if frequency in (Frequency.DAILY, Frequency.WEEKLY, Frequency.FORTNIGHTLY):
        step = {Frequency.DAILY: 1, Frequency.WEEKLY: 7, Frequency.FORTNIGHTLY: 14}[
            frequency
        ] * interval
        index = max(0, (after - start).days // step)

        def compute(i):
            return start + timedelta(days=step * i)
    else:
        step = interval * (12 if frequency == Frequency.YEARLY else 1)
        distance = (after.year - start.year) * 12 + after.month - start.month
        index = max(0, distance // step)

        def compute(i):
            return add_months(start, step * i)

    result = []
    while (current := compute(index)) <= limit:
        if current >= after:
            result.append(current)
        index += 1
    return result


def invoice_status(total: Decimal, paid: Decimal, closing: date, due: date, today: date) -> str:
    outstanding = total - paid
    if outstanding <= 0:
        return "paid"
    if outstanding > 0 and today > due:
        return "overdue"
    if paid > 0:
        return "partially_paid"
    return "closed" if today > closing else "open"
