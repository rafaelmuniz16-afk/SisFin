"""Contratos de saída dos endpoints financeiros principais, publicados no OpenAPI."""

from datetime import date, datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from app.domain import AccountKind, NodeKind, PaymentMethod, TransactionKind, TransactionStatus


class Row(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    family_id: UUID
    created_at: datetime


class AccountView(Row):
    name: str
    kind: AccountKind
    bank: str | None
    ledger_account_id: UUID
    owner_id: UUID
    active: bool
    allow_overdraft: bool
    balance: Decimal


class CardView(Row):
    name: str
    ledger_account_id: UUID
    owner_id: UUID
    payment_account_id: UUID
    credit_limit: Decimal
    used_limit: Decimal
    available_limit: Decimal
    credit_balance: Decimal
    closing_day: int
    due_day: int
    next_best_purchase_date: date
    last_four: str | None
    active: bool


class TransactionView(Row):
    kind: TransactionKind
    node: NodeKind
    status: TransactionStatus
    amount: Decimal
    description: str
    merchant: str | None
    tags: list[str]
    details: dict
    competency_date: date
    purchase_date: date
    billing_date: date | None
    due_date: date | None
    paid_date: date | None
    payment_method: PaymentMethod
    account_id: UUID | None
    destination_account_id: UUID | None
    card_id: UUID | None
    invoice_id: UUID | None
    category_id: UUID | None
    parent_id: UUID | None
    related_transaction_id: UUID | None
    recurrence_id: UUID | None
    occurrence_date: date | None
    installment_number: int | None
    installment_count: int | None
    created_by: UUID


class InvoiceView(Row):
    card_id: UUID
    cycle_month: date
    closing_date: date
    due_date: date
    total: Decimal
    paid: Decimal
    outstanding: Decimal
    credit_balance: Decimal
    status: Literal["open", "closed", "partially_paid", "paid", "overdue"]


class PurchaseView(BaseModel):
    purchase: TransactionView
    installments: list[TransactionView]
    card: CardView


class InvoicePaymentView(BaseModel):
    payment: TransactionView
    invoice: InvoiceView
    card: CardView
    account_balance: Decimal


class TokenView(BaseModel):
    access_token: str
    token_type: Literal["bearer"]
