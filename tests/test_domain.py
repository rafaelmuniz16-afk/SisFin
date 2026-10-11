from datetime import date
from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.domain import (
    Frequency,
    add_months,
    installments,
    invoice_cycle,
    invoice_status,
    money,
    occurrences,
)
from app.schemas import CreditPurchase, DateRange, Register
from app.services.common import public


@pytest.mark.parametrize(
    "total,count,expected",
    [
        ("100.00", 3, ["33.34", "33.33", "33.33"]),
        ("0.03", 3, ["0.01", "0.01", "0.01"]),
        ("0.05", 2, ["0.03", "0.02"]),
        ("1200.00", 12, ["100.00"] * 12),
        ("9999999999.99", 1, ["9999999999.99"]),
    ],
)
def test_cent_conservation(total, count, expected):
    actual = installments(Decimal(total), count)
    assert actual == [Decimal(x) for x in expected]
    assert sum(actual) == Decimal(total)


def test_many_cent_distributions():
    for cents in [1, 7, 10, 99, 100, 101, 9999, 10000, 360001]:
        for count in range(1, min(cents, 600) + 1):
            parts = installments(Decimal(cents) / 100, count)
            assert sum(parts) == Decimal(cents) / 100
            assert max(parts) - min(parts) <= Decimal("0.01")
            assert all(p > 0 and p == p.quantize(Decimal("0.01")) for p in parts)


@pytest.mark.parametrize(
    "bad", [0.1, True, "0.001", "NaN", "Infinity", "1e999999", "10000000000", {}, None]
)
def test_money_rejects_invalid_or_imprecise_values(bad):
    with pytest.raises(ValueError):
        money(bad)


@pytest.mark.parametrize(
    "total,count", [("1.00", 0), ("0.00", 1), ("-1.00", 1), ("0.01", 2), ("100", 601)]
)
def test_invalid_installments(total, count):
    with pytest.raises(ValueError):
        installments(Decimal(total), count)


@pytest.mark.parametrize(
    "purchase,closing,due,cycle,close_date,due_date",
    [
        ("2026-09-02", 2, 10, "2026-09-01", "2026-09-02", "2026-09-10"),
        ("2026-09-03", 2, 10, "2026-10-01", "2026-10-02", "2026-10-10"),
        ("2026-12-31", 20, 5, "2027-01-01", "2027-01-20", "2027-02-05"),
        ("2026-02-28", 31, 31, "2026-02-01", "2026-02-28", "2026-03-31"),
        ("2028-02-29", 30, 5, "2028-02-01", "2028-02-29", "2028-03-05"),
        ("2026-09-20", 20, 20, "2026-09-01", "2026-09-20", "2026-10-20"),
    ],
)
def test_cycle_boundaries(purchase, closing, due, cycle, close_date, due_date):
    assert invoice_cycle(date.fromisoformat(purchase), closing, due) == tuple(
        date.fromisoformat(x) for x in [cycle, close_date, due_date]
    )


def test_anchored_monthly_and_leap_recurrences():
    assert occurrences(
        date(2026, 1, 31), None, Frequency.MONTHLY, 1, date(2026, 1, 1), date(2026, 4, 30)
    ) == [date(2026, 1, 31), date(2026, 2, 28), date(2026, 3, 31), date(2026, 4, 30)]
    assert occurrences(
        date(2024, 2, 29), None, Frequency.YEARLY, 1, date(2024, 1, 1), date(2028, 12, 31)
    )[-1] == date(2028, 2, 29)


def test_fortnight_means_fourteen_days():
    assert occurrences(
        date(2026, 1, 1),
        date(2026, 2, 1),
        Frequency.FORTNIGHTLY,
        1,
        date(2026, 1, 1),
        date(2026, 3, 1),
    ) == [date(2026, 1, 1), date(2026, 1, 15), date(2026, 1, 29)]


@pytest.mark.parametrize(
    "total,paid,today,status",
    [
        ("100", "0", "2026-09-02", "open"),
        ("100", "0", "2026-09-03", "closed"),
        ("100", "30", "2026-09-03", "partially_paid"),
        ("100", "30", "2026-09-11", "overdue"),
        ("100", "100", "2026-09-11", "paid"),
        ("0", "0", "2026-09-11", "paid"),
        ("-50", "0", "2026-09-11", "paid"),
    ],
)
def test_status_priority(total, paid, today, status):
    assert (
        invoice_status(
            Decimal(total),
            Decimal(paid),
            date(2026, 9, 2),
            date(2026, 9, 10),
            date.fromisoformat(today),
        )
        == status
    )


def test_split_requires_exact_total_and_one_allocation_mode():
    base = dict(
        amount="100.00",
        description="Supermercado",
        competency_date="2026-09-01",
        purchase_date="2026-09-01",
    )
    cat1, cat2 = "00000000-0000-0000-0000-000000000001", "00000000-0000-0000-0000-000000000002"
    CreditPurchase(
        **base,
        splits=[{"category_id": cat1, "amount": "70.00"}, {"category_id": cat2, "amount": "30.00"}],
    )
    for extra in [
        {},
        {"category_id": cat1, "splits": [{"category_id": cat1, "amount": "100"}]},
        {"splits": [{"category_id": cat1, "amount": "99.99"}]},
    ]:
        with pytest.raises(ValidationError):
            CreditPurchase(**base, **extra)


def test_unicode_password_limit_in_bytes_and_whitespace_preserved():
    with pytest.raises(ValidationError):
        Register(email="test@example.com", name="Test", password="á" * 37)
    user = Register(email="test@example.com", name="Test", password=" long password with spaces ")
    assert user.password.startswith(" ") and user.password.endswith(" ")


def test_bounded_forecast_and_json_precision():
    with pytest.raises(ValidationError):
        DateRange(start="2026-01-01", end="2040-01-01")
    assert public({"amount": Decimal("9999999999.99")}) == {"amount": "9999999999.99"}
    assert add_months(date(2026, 12, 31), 2) == date(2027, 2, 28)
