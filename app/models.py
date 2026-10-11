"""SQLAlchemy 2.0; família em todas as FKs de domínio impede vínculos cruzados.

O modo assíncrono pertence a engine/AsyncSession; os models são declarativos tipados.
"""

from datetime import date, datetime
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    MetaData,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, declared_attr, mapped_column

from app.domain import (
    AccountKind,
    Frequency,
    LedgerKind,
    NodeKind,
    PaymentMethod,
    Role,
    TransactionKind,
    TransactionStatus,
)

MONEY = Numeric(12, 2)


def choice(enum):
    return Enum(
        enum,
        native_enum=False,
        create_constraint=True,
        values_callable=lambda cls: [x.value for x in cls],
    )


class Base(DeclarativeBase):
    metadata = MetaData(
        naming_convention={
            "ix": "ix_%(table_name)s_%(column_0_name)s",
            "uq": "uq_%(table_name)s_%(column_0_N_name)s",
            "ck": "ck_%(table_name)s_%(constraint_name)s",
            "fk": "fk_%(table_name)s_%(column_0_N_name)s_%(referred_table_name)s",
            "pk": "pk_%(table_name)s",
        }
    )


class Identity:
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Tenant(Identity):
    family_id: Mapped[UUID] = mapped_column(
        ForeignKey("families.id", ondelete="RESTRICT"), index=True
    )

    @declared_attr.directive
    def __table_args__(cls):
        return (UniqueConstraint("family_id", "id", name=f"uq_{cls.__tablename__}_family_id"),)


def tenant_constraints(table: str, *extras):
    return (UniqueConstraint("family_id", "id", name=f"uq_{table}_family_id"), *extras)


def tenant_fk(column: str, target: str, extra: str | None = None):
    return ForeignKeyConstraint(
        ["family_id", column],
        [f"{target}.family_id", f"{target}.{extra or 'id'}"],
        ondelete="RESTRICT",
    )


class User(Identity, Base):
    __tablename__ = "users"
    email: Mapped[str] = mapped_column(String(320), unique=True)
    name: Mapped[str] = mapped_column(String(120))
    password_hash: Mapped[str] = mapped_column(String(100))
    active: Mapped[bool] = mapped_column(default=True)
    token_version: Mapped[int] = mapped_column(default=0)
    __table_args__ = (CheckConstraint("email = lower(email)", name="lower_email"),)


class Family(Identity, Base):
    __tablename__ = "families"
    name: Mapped[str] = mapped_column(String(120))
    currency: Mapped[str] = mapped_column(String(3), default="BRL")
    timezone: Mapped[str] = mapped_column(String(64), default="America/Sao_Paulo")
    __table_args__ = (CheckConstraint("currency = 'BRL'", name="single_currency"),)


class FamilyMember(Base):
    __tablename__ = "family_members"
    family_id: Mapped[UUID] = mapped_column(ForeignKey("families.id"), primary_key=True)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), primary_key=True)
    role: Mapped[Role] = mapped_column(choice(Role))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    __table_args__ = (Index("ix_family_members_user_id", "user_id", "family_id"),)


class LedgerAccount(Tenant, Base):
    __tablename__ = "ledger_accounts"
    name: Mapped[str] = mapped_column(String(160))
    kind: Mapped[LedgerKind] = mapped_column(choice(LedgerKind))
    system_key: Mapped[str | None] = mapped_column(String(50))
    __table_args__ = tenant_constraints(__tablename__, UniqueConstraint("family_id", "system_key"))


class Account(Tenant, Base):
    __tablename__ = "accounts"
    name: Mapped[str] = mapped_column(String(120))
    kind: Mapped[AccountKind] = mapped_column(choice(AccountKind))
    ledger_account_id: Mapped[UUID] = mapped_column(unique=True)
    owner_id: Mapped[UUID] = mapped_column()
    bank: Mapped[str | None] = mapped_column(String(120))
    active: Mapped[bool] = mapped_column(default=True)
    allow_overdraft: Mapped[bool] = mapped_column(default=False)
    __table_args__ = tenant_constraints(
        __tablename__,
        tenant_fk("ledger_account_id", "ledger_accounts"),
        tenant_fk("owner_id", "family_members", "user_id"),
    )


class CreditCard(Tenant, Base):
    __tablename__ = "credit_cards"
    name: Mapped[str] = mapped_column(String(120))
    ledger_account_id: Mapped[UUID] = mapped_column(unique=True)
    owner_id: Mapped[UUID] = mapped_column()
    payment_account_id: Mapped[UUID] = mapped_column()
    credit_limit: Mapped[Decimal] = mapped_column(MONEY)
    closing_day: Mapped[int] = mapped_column(Integer)
    due_day: Mapped[int] = mapped_column(Integer)
    last_four: Mapped[str | None] = mapped_column(String(4))
    active: Mapped[bool] = mapped_column(default=True)
    __table_args__ = tenant_constraints(
        __tablename__,
        tenant_fk("ledger_account_id", "ledger_accounts"),
        tenant_fk("payment_account_id", "accounts"),
        tenant_fk("owner_id", "family_members", "user_id"),
        CheckConstraint("credit_limit >= 0", name="positive_limit"),
        CheckConstraint("closing_day BETWEEN 1 AND 31 AND due_day BETWEEN 1 AND 31", name="days"),
        CheckConstraint("last_four IS NULL OR last_four ~ '^[0-9]{4}$'", name="last_four"),
    )


class Category(Tenant, Base):
    __tablename__ = "categories"
    name: Mapped[str] = mapped_column(String(120))
    parent_id: Mapped[UUID | None] = mapped_column()
    kind: Mapped[str] = mapped_column(String(10))
    __table_args__ = tenant_constraints(
        __tablename__,
        tenant_fk("parent_id", "categories"),
        CheckConstraint("kind IN ('income', 'expense')", name="kind"),
        CheckConstraint("parent_id IS NULL OR parent_id <> id", name="not_self"),
    )


class Invoice(Tenant, Base):
    __tablename__ = "invoices"
    card_id: Mapped[UUID] = mapped_column()
    cycle_month: Mapped[date] = mapped_column(Date)
    closing_date: Mapped[date] = mapped_column(Date)
    due_date: Mapped[date] = mapped_column(Date)
    # Datas são snapshot do contrato no instante da criação; estado é derivado.
    __table_args__ = tenant_constraints(
        __tablename__,
        tenant_fk("card_id", "credit_cards"),
        UniqueConstraint("family_id", "card_id", "cycle_month"),
        CheckConstraint("EXTRACT(DAY FROM cycle_month) = 1", name="month_first"),
        CheckConstraint(
            "closing_date >= cycle_month AND closing_date < cycle_month + interval '1 month'",
            name="closing_in_cycle",
        ),
        CheckConstraint("due_date > closing_date", name="due_after_close"),
        Index("ix_invoices_family_due", "family_id", "due_date"),
    )


class Recurrence(Tenant, Base):
    __tablename__ = "recurrences"
    name: Mapped[str] = mapped_column(String(160))
    kind: Mapped[str] = mapped_column(String(10))
    amount: Mapped[Decimal] = mapped_column(MONEY)
    variable: Mapped[bool] = mapped_column(default=False)
    subscription: Mapped[bool] = mapped_column(default=False)
    frequency: Mapped[Frequency] = mapped_column(choice(Frequency))
    interval: Mapped[int] = mapped_column(default=1)
    start_date: Mapped[date] = mapped_column(Date)
    end_date: Mapped[date | None] = mapped_column(Date)
    category_id: Mapped[UUID] = mapped_column()
    account_id: Mapped[UUID | None] = mapped_column()
    card_id: Mapped[UUID | None] = mapped_column()
    payment_method: Mapped[PaymentMethod] = mapped_column(choice(PaymentMethod))
    active: Mapped[bool] = mapped_column(default=True)
    __table_args__ = tenant_constraints(
        __tablename__,
        tenant_fk("category_id", "categories"),
        tenant_fk("account_id", "accounts"),
        tenant_fk("card_id", "credit_cards"),
        CheckConstraint("amount > 0 AND interval BETWEEN 1 AND 120", name="values"),
        CheckConstraint("end_date IS NULL OR end_date >= start_date", name="dates"),
        CheckConstraint("kind IN ('income','expense')", name="kind"),
        CheckConstraint(
            "(card_id IS NOT NULL AND account_id IS NULL AND kind='expense' AND payment_method='credit') OR (account_id IS NOT NULL AND card_id IS NULL AND payment_method<>'credit')",
            name="funding",
        ),
    )


class Transaction(Tenant, Base):
    __tablename__ = "transactions"
    kind: Mapped[TransactionKind] = mapped_column(choice(TransactionKind))
    node: Mapped[NodeKind] = mapped_column(choice(NodeKind), default=NodeKind.ROOT)
    status: Mapped[TransactionStatus] = mapped_column(
        choice(TransactionStatus), default=TransactionStatus.POSTED
    )
    amount: Mapped[Decimal] = mapped_column(MONEY)
    description: Mapped[str] = mapped_column(String(240))
    merchant: Mapped[str | None] = mapped_column(String(160))
    tags: Mapped[list[str]] = mapped_column(ARRAY(String(64)), default=list)
    details: Mapped[dict] = mapped_column(JSONB, default=dict)
    competency_date: Mapped[date] = mapped_column(Date)
    purchase_date: Mapped[date] = mapped_column(Date)
    billing_date: Mapped[date | None] = mapped_column(Date)
    due_date: Mapped[date | None] = mapped_column(Date)
    paid_date: Mapped[date | None] = mapped_column(Date)
    payment_method: Mapped[PaymentMethod] = mapped_column(choice(PaymentMethod))
    account_id: Mapped[UUID | None] = mapped_column()
    destination_account_id: Mapped[UUID | None] = mapped_column()
    card_id: Mapped[UUID | None] = mapped_column()
    invoice_id: Mapped[UUID | None] = mapped_column()
    category_id: Mapped[UUID | None] = mapped_column()
    parent_id: Mapped[UUID | None] = mapped_column()
    related_transaction_id: Mapped[UUID | None] = mapped_column()
    recurrence_id: Mapped[UUID | None] = mapped_column()
    occurrence_date: Mapped[date | None] = mapped_column(Date)
    installment_number: Mapped[int | None] = mapped_column(Integer)
    installment_count: Mapped[int | None] = mapped_column(Integer)
    created_by: Mapped[UUID] = mapped_column(ForeignKey("users.id"))
    __table_args__ = tenant_constraints(
        __tablename__,
        *[
            tenant_fk(col, target)
            for col, target in (
                ("account_id", "accounts"),
                ("destination_account_id", "accounts"),
                ("card_id", "credit_cards"),
                ("invoice_id", "invoices"),
                ("category_id", "categories"),
                ("parent_id", "transactions"),
                ("related_transaction_id", "transactions"),
                ("recurrence_id", "recurrences"),
            )
        ],
        CheckConstraint("amount > 0", name="positive_amount"),
        CheckConstraint(
            "(node='root' AND parent_id IS NULL) OR (node<>'root' AND parent_id IS NOT NULL)",
            name="node_parent",
        ),
        CheckConstraint(
            "(node='installment' AND invoice_id IS NOT NULL AND card_id IS NOT NULL AND installment_number IS NOT NULL AND installment_count IS NOT NULL AND installment_count BETWEEN 1 AND 600 AND installment_number BETWEEN 1 AND installment_count) OR (node<>'installment' AND installment_number IS NULL)",
            name="installment",
        ),
        CheckConstraint("node<>'split' OR category_id IS NOT NULL", name="split_category"),
        CheckConstraint(
            "NOT (node='root' AND kind='expense' AND card_id IS NOT NULL AND status='posted') OR (installment_count IS NOT NULL AND installment_count BETWEEN 1 AND 600)",
            name="credit_purchase_count",
        ),
        CheckConstraint(
            "(recurrence_id IS NULL) = (occurrence_date IS NULL)", name="occurrence_pair"
        ),
        UniqueConstraint("family_id", "parent_id", "installment_number"),
        UniqueConstraint("family_id", "recurrence_id", "occurrence_date"),
        Index("ix_transactions_family_competency", "family_id", "competency_date"),
        Index("ix_transactions_tags", "tags", postgresql_using="gin"),
        Index("ix_transactions_family_parent", "family_id", "parent_id"),
        Index("ix_transactions_family_related", "family_id", "related_transaction_id"),
    )


class InvoiceItem(Tenant, Base):
    __tablename__ = "invoice_items"
    invoice_id: Mapped[UUID] = mapped_column()
    transaction_id: Mapped[UUID] = mapped_column()
    amount: Mapped[Decimal] = mapped_column(MONEY)  # crédito de estorno é negativo
    __table_args__ = tenant_constraints(
        __tablename__,
        tenant_fk("invoice_id", "invoices"),
        tenant_fk("transaction_id", "transactions"),
        CheckConstraint("amount <> 0", name="nonzero"),
        UniqueConstraint("family_id", "invoice_id", "transaction_id"),
    )


class InvoicePayment(Tenant, Base):
    __tablename__ = "invoice_payments"
    invoice_id: Mapped[UUID] = mapped_column()
    transaction_id: Mapped[UUID] = mapped_column(unique=True)
    amount: Mapped[Decimal] = mapped_column(MONEY)
    payment_date: Mapped[date] = mapped_column(Date)
    __table_args__ = tenant_constraints(
        __tablename__,
        tenant_fk("invoice_id", "invoices"),
        tenant_fk("transaction_id", "transactions"),
        CheckConstraint("amount > 0", name="positive"),
    )


class CashSettlement(Tenant, Base):
    __tablename__ = "cash_settlements"
    original_transaction_id: Mapped[UUID] = mapped_column()
    settlement_transaction_id: Mapped[UUID] = mapped_column(unique=True)
    amount: Mapped[Decimal] = mapped_column(MONEY)
    payment_date: Mapped[date] = mapped_column(Date)
    __table_args__ = tenant_constraints(
        __tablename__,
        tenant_fk("original_transaction_id", "transactions"),
        tenant_fk("settlement_transaction_id", "transactions"),
        CheckConstraint("amount > 0", name="positive"),
    )


class Journal(Tenant, Base):
    __tablename__ = "journals"
    transaction_id: Mapped[UUID] = mapped_column()
    event: Mapped[str] = mapped_column(String(30))
    economic_date: Mapped[date] = mapped_column(Date)
    effective_date: Mapped[date] = mapped_column(Date)
    __table_args__ = tenant_constraints(
        __tablename__,
        tenant_fk("transaction_id", "transactions"),
        UniqueConstraint("family_id", "transaction_id", "event"),
        Index("ix_journals_economic", "family_id", "economic_date"),
        Index("ix_journals_effective", "family_id", "effective_date"),
    )


class JournalLine(Tenant, Base):
    __tablename__ = "journal_lines"
    journal_id: Mapped[UUID] = mapped_column()
    ledger_account_id: Mapped[UUID] = mapped_column()
    category_id: Mapped[UUID | None] = mapped_column()
    debit: Mapped[Decimal] = mapped_column(MONEY, default=0)
    credit: Mapped[Decimal] = mapped_column(MONEY, default=0)
    __table_args__ = tenant_constraints(
        __tablename__,
        tenant_fk("journal_id", "journals"),
        tenant_fk("ledger_account_id", "ledger_accounts"),
        tenant_fk("category_id", "categories"),
        CheckConstraint(
            "(debit > 0 AND credit = 0) OR (credit > 0 AND debit = 0)", name="one_side"
        ),
        Index("ix_lines_balance", "family_id", "ledger_account_id"),
        Index("ix_lines_category", "family_id", "category_id"),
    )


class TransactionShare(Tenant, Base):
    __tablename__ = "transaction_shares"
    transaction_id: Mapped[UUID] = mapped_column()
    user_id: Mapped[UUID] = mapped_column()
    amount: Mapped[Decimal] = mapped_column(MONEY)
    __table_args__ = tenant_constraints(
        __tablename__,
        tenant_fk("transaction_id", "transactions"),
        tenant_fk("user_id", "family_members", "user_id"),
        CheckConstraint("amount > 0", name="positive"),
        UniqueConstraint("family_id", "transaction_id", "user_id"),
    )


class Attachment(Tenant, Base):
    __tablename__ = "attachments"
    transaction_id: Mapped[UUID] = mapped_column()
    url: Mapped[str] = mapped_column(Text)
    name: Mapped[str] = mapped_column(String(240))
    __table_args__ = tenant_constraints(__tablename__, tenant_fk("transaction_id", "transactions"))


class Budget(Tenant, Base):
    __tablename__ = "budgets"
    category_id: Mapped[UUID] = mapped_column()
    month: Mapped[date] = mapped_column(Date)
    amount: Mapped[Decimal] = mapped_column(MONEY)
    rollover: Mapped[bool] = mapped_column(default=False)
    __table_args__ = tenant_constraints(
        __tablename__,
        tenant_fk("category_id", "categories"),
        CheckConstraint("amount >= 0 AND EXTRACT(DAY FROM month)=1", name="values"),
        UniqueConstraint("family_id", "category_id", "month"),
    )


class Goal(Tenant, Base):
    __tablename__ = "goals"
    name: Mapped[str] = mapped_column(String(160))
    account_id: Mapped[UUID] = mapped_column()
    target_amount: Mapped[Decimal] = mapped_column(MONEY)
    target_date: Mapped[date] = mapped_column(Date)
    monthly_contribution: Mapped[Decimal] = mapped_column(MONEY, default=0)
    __table_args__ = tenant_constraints(
        __tablename__,
        tenant_fk("account_id", "accounts"),
        CheckConstraint("target_amount > 0 AND monthly_contribution >= 0", name="values"),
    )


class GoalContribution(Tenant, Base):
    __tablename__ = "goal_contributions"
    goal_id: Mapped[UUID] = mapped_column()
    amount: Mapped[Decimal] = mapped_column(MONEY)
    date: Mapped[date] = mapped_column(Date)
    __table_args__ = tenant_constraints(
        __tablename__, tenant_fk("goal_id", "goals"), CheckConstraint("amount <> 0", name="nonzero")
    )


class WealthPosition(Tenant, Base):
    __tablename__ = "wealth_positions"
    name: Mapped[str] = mapped_column(String(160))
    kind: Mapped[str] = mapped_column(String(10))
    ledger_account_id: Mapped[UUID] = mapped_column(unique=True)
    __table_args__ = tenant_constraints(
        __tablename__,
        tenant_fk("ledger_account_id", "ledger_accounts"),
        CheckConstraint("kind IN ('asset','liability')", name="kind"),
    )


class IdempotencyRecord(Tenant, Base):
    __tablename__ = "idempotency_records"
    key: Mapped[str] = mapped_column(String(128))
    actor_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"))
    operation: Mapped[str] = mapped_column(String(240))
    request_hash: Mapped[str] = mapped_column(String(64))
    response: Mapped[dict] = mapped_column(JSONB)
    __table_args__ = tenant_constraints(__tablename__, UniqueConstraint("family_id", "key"))


class AuditLog(Tenant, Base):
    __tablename__ = "audit_logs"
    actor_id: Mapped[UUID | None] = mapped_column(ForeignKey("users.id"))
    table_name: Mapped[str] = mapped_column(String(80))
    row_key: Mapped[str] = mapped_column(String(160))
    action: Mapped[str] = mapped_column(String(10))
    old_value: Mapped[dict | None] = mapped_column(JSONB)
    new_value: Mapped[dict | None] = mapped_column(JSONB)
    __table_args__ = tenant_constraints(
        __tablename__, Index("ix_audit_family_time", "family_id", "created_at")
    )
