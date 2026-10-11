from datetime import date
from decimal import Decimal
from typing import Annotated, Literal
from uuid import UUID
from zoneinfo import ZoneInfo

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    EmailStr,
    Field,
    HttpUrl,
    field_validator,
    model_validator,
)

from app.domain import AccountKind, Frequency, PaymentMethod, Role, money

Money = Annotated[Decimal, BeforeValidator(money), Field(max_digits=12, decimal_places=2)]
PositiveMoney = Annotated[Money, Field(gt=0)]
NonnegativeMoney = Annotated[Money, Field(ge=0)]


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class Register(Input):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=False)
    email: EmailStr
    name: str = Field(min_length=1, max_length=120)
    password: str = Field(min_length=12, max_length=72)

    @field_validator("password")
    @classmethod
    def bcrypt_bytes(cls, value):
        if len(value.encode("utf-8")) > 72:
            raise ValueError("bcrypt admite até 72 bytes UTF-8; a senha não será truncada.")
        return value


class FamilyCreate(Input):
    name: str = Field(min_length=1, max_length=120)
    timezone: str = "America/Sao_Paulo"

    @field_validator("timezone")
    @classmethod
    def timezone_exists(cls, value):
        try:
            ZoneInfo(value)
        except (KeyError, ValueError):
            raise ValueError("Fuso horário IANA inválido.")
        return value


class MemberInput(Input):
    user_id: UUID
    role: Role


class CategoryCreate(Input):
    name: str = Field(min_length=1, max_length=120)
    kind: Literal["income", "expense"]
    parent_id: UUID | None = None


class AccountCreate(Input):
    name: str = Field(min_length=1, max_length=120)
    kind: AccountKind
    bank: str | None = Field(default=None, max_length=120)
    opening_balance: Money = Decimal("0.00")
    opening_date: date
    allow_overdraft: bool = False
    owner_id: UUID | None = None


class CardCreate(Input):
    name: str = Field(min_length=1, max_length=120)
    credit_limit: NonnegativeMoney
    closing_day: int = Field(ge=1, le=31)
    due_day: int = Field(ge=1, le=31)
    payment_account_id: UUID
    owner_id: UUID | None = None
    last_four: str | None = Field(default=None, pattern=r"^\d{4}$")


class Split(Input):
    category_id: UUID
    amount: PositiveMoney


class Share(Input):
    user_id: UUID
    amount: PositiveMoney


class Receipt(Input):
    name: str = Field(min_length=1, max_length=240)
    url: HttpUrl


class ExpenseBase(Input):
    amount: PositiveMoney
    description: str = Field(min_length=1, max_length=240)
    competency_date: date
    purchase_date: date
    merchant: str | None = Field(default=None, max_length=160)
    tags: list[Annotated[str, Field(min_length=1, max_length=64)]] = Field(
        default_factory=list, max_length=30
    )
    category_id: UUID | None = None
    splits: list[Split] = Field(default_factory=list, max_length=100)
    shares: list[Share] = Field(default_factory=list, max_length=100)
    attachments: list[Receipt] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def check_allocation(self):
        if bool(self.category_id) == bool(self.splits):
            raise ValueError("Informe uma categoria OU uma lista de rateios.")
        if self.splits and sum((s.amount for s in self.splits), Decimal(0)) != self.amount:
            raise ValueError("A soma dos rateios deve ser igual ao total.")
        if len({s.category_id for s in self.splits}) != len(self.splits):
            raise ValueError("Categorias repetidas no rateio.")
        if self.shares and sum((s.amount for s in self.shares), Decimal(0)) != self.amount:
            raise ValueError("A soma das participações deve ser igual ao total.")
        if len({s.user_id for s in self.shares}) != len(self.shares):
            raise ValueError("Participante repetido.")
        return self


class CreditPurchase(ExpenseBase):
    installments: int = Field(default=1, ge=1, le=600)
    billing_date: date | None = None


class CashTransaction(ExpenseBase):
    kind: Literal["income", "expense"]
    account_id: UUID
    payment_method: PaymentMethod
    due_date: date
    payment_date: date | None = None

    @field_validator("payment_method")
    @classmethod
    def not_credit(cls, value):
        if value == PaymentMethod.CREDIT:
            raise ValueError("Use /cards/{id}/purchases para crédito.")
        return value


class Payment(Input):
    account_id: UUID
    amount: PositiveMoney
    payment_date: date
    payment_method: PaymentMethod = PaymentMethod.PIX

    @field_validator("payment_method")
    @classmethod
    def not_credit(cls, value):
        if value == PaymentMethod.CREDIT:
            raise ValueError("Pagamento exige uma conta real.")
        return value


class Transfer(Input):
    source_account_id: UUID
    destination_account_id: UUID
    amount: PositiveMoney
    date: date
    description: str = Field(default="Transferência entre contas", min_length=1, max_length=240)

    @model_validator(mode="after")
    def different(self):
        if self.source_account_id == self.destination_account_id:
            raise ValueError("Origem e destino devem ser diferentes.")
        return self


class Refund(Input):
    kind: Literal["reimbursement", "reversal"]
    amount: PositiveMoney
    date: date
    account_id: UUID | None = None
    invoice_id: UUID | None = None
    # Categoria é explícita em compras divididas para não inventar o rateio do estorno.
    category_id: UUID
    description: str = Field(default="Devolução vinculada", min_length=1, max_length=240)

    @model_validator(mode="after")
    def destination(self):
        if bool(self.account_id) == bool(self.invoice_id):
            raise ValueError("Informe conta de recebimento OU fatura de crédito.")
        if self.kind == "reimbursement" and not self.account_id:
            raise ValueError("Reembolso de terceiro deve entrar em conta real.")
        return self


class Adjustment(Input):
    delta: Money
    date: date
    reason: str = Field(min_length=5, max_length=240)

    @field_validator("delta")
    @classmethod
    def nonzero(cls, value):
        if value == 0:
            raise ValueError("Ajuste não pode ser zero.")
        return value


class CreditTransfer(Input):
    destination_invoice_id: UUID
    amount: PositiveMoney
    date: date


class RecurrenceCreate(Input):
    name: str = Field(min_length=1, max_length=160)
    kind: Literal["income", "expense"]
    amount: PositiveMoney
    variable: bool = False
    subscription: bool = False
    frequency: Frequency
    interval: int = Field(default=1, ge=1, le=120)
    start_date: date
    end_date: date | None = None
    category_id: UUID
    account_id: UUID | None = None
    card_id: UUID | None = None
    payment_method: PaymentMethod

    @model_validator(mode="after")
    def funding(self):
        if self.end_date and self.end_date < self.start_date:
            raise ValueError("Data final anterior à inicial.")
        if self.card_id:
            if (
                self.account_id
                or self.payment_method != PaymentMethod.CREDIT
                or self.kind != "expense"
            ):
                raise ValueError("Recorrência no cartão deve ser despesa a crédito.")
        elif not self.account_id or self.payment_method == PaymentMethod.CREDIT:
            raise ValueError("Informe uma conta e forma de pagamento sem crédito.")
        return self


class DateRange(Input):
    start: date
    end: date

    @model_validator(mode="after")
    def bounded(self):
        if not 0 <= (self.end - self.start).days <= 1096:
            raise ValueError("Período deve ter entre zero e 1.096 dias.")
        return self


class Confirm(Input):
    amount: PositiveMoney | None = None
    payment_date: date | None = None


class BudgetCreate(Input):
    category_id: UUID
    month: date
    amount: NonnegativeMoney
    rollover: bool = False

    @field_validator("month")
    @classmethod
    def first_day(cls, value):
        if value.day != 1:
            raise ValueError("Mês deve ser informado como AAAA-MM-01.")
        return value


class GoalCreate(Input):
    name: str = Field(min_length=1, max_length=160)
    account_id: UUID
    target_amount: PositiveMoney
    target_date: date
    monthly_contribution: NonnegativeMoney = Decimal("0.00")


class Contribution(Input):
    amount: Money
    date: date

    @field_validator("amount")
    @classmethod
    def nonzero(cls, value):
        if not value:
            raise ValueError("Contribuição deve ser diferente de zero.")
        return value


class PositionCreate(Input):
    name: str = Field(min_length=1, max_length=160)
    kind: Literal["asset", "liability"]
    value: NonnegativeMoney
    date: date


class Valuation(Input):
    value: NonnegativeMoney
    date: date
    reason: str = Field(min_length=5, max_length=240)
