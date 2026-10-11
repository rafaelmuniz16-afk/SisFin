"""Testes de aceitação contra PostgreSQL REAL: migrations, HTTP e concorrência.

Não substituir por SQLite: locks, RLS e constraints diferidas são parte do contrato.
"""

import asyncio
from datetime import datetime, timedelta
from decimal import Decimal
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError

from app import models as m
from app.db import sessions

pytestmark = pytest.mark.integration


def purchase(family, amount="1200.00", count=12, **extra):
    return {
        "amount": amount,
        "installments": count,
        "description": "Notebook",
        "competency_date": "2026-09-03",
        "purchase_date": "2026-09-03",
        "category_id": family["category"]["id"],
        **extra,
    }


async def report(api, path="economic"):
    return (
        await api.get(f"/reports/{path}", params={"start": "2026-01-01", "end": "2026-12-31"})
    ).json()


async def test_credit_and_invoice_payment_preserve_golden_rule(api, family):
    buy = await family["write"](f"/cards/{family['card']['id']}/purchases", purchase(family))
    assert buy["card"]["available_limit"] == "800.00"
    assert len(buy["installments"]) == 12
    assert buy["installments"][0]["due_date"] == "2026-10-10"
    assert buy["installments"][-1]["due_date"] == "2027-09-10"
    account = (await api.get(f"/accounts/{family['account']['id']}")).json()
    assert account["balance"] == "5000.00"
    assert (await report(api))["expense"] == "1200.00"
    before = (await api.get("/reports/net-worth")).json()
    payment = await family["write"](
        f"/invoices/{buy['installments'][0]['invoice_id']}/payments",
        {"account_id": account["id"], "amount": "100.00", "payment_date": "2026-09-05"},
    )
    assert payment["payment"]["kind"] == "transfer"
    assert payment["invoice"]["status"] == "paid"
    assert payment["account_balance"] == "4900.00"
    assert payment["card"]["available_limit"] == "900.00"
    assert (await report(api))["expense"] == "1200.00"
    assert (
        (await api.get("/reports/net-worth")).json()["net_worth"]
        == before["net_worth"]
        == "3800.00"
    )
    assert (await report(api, "cash-flow"))["gross_outflow"] == "100.00"


async def test_fractional_purchase_and_concurrent_idempotency(api, family):
    path = f"/cards/{family['card']['id']}/purchases"
    body = purchase(family, "100.00", 3)
    key = str(uuid4())
    results = await asyncio.gather(
        *[api.post(path, json=body, headers={"Idempotency-Key": key}) for _ in range(2)]
    )
    assert [r.status_code for r in results] == [201, 201]
    assert results[0].json() == results[1].json()
    assert [r["amount"] for r in results[0].json()["installments"]] == ["33.34", "33.33", "33.33"]
    assert (await report(api))["expense"] == "100.00"
    changed = await api.post(
        path, json=purchase(family, "110.00", 3), headers={"Idempotency-Key": key}
    )
    assert changed.status_code == 409


async def test_simultaneous_purchases_cannot_exceed_full_limit(api, family):
    path = f"/cards/{family['card']['id']}/purchases"
    results = await asyncio.gather(
        *[
            api.post(
                path,
                json=purchase(family, "1500.00", 12),
                headers={"Idempotency-Key": str(uuid4())},
            )
            for _ in range(2)
        ]
    )
    assert sorted(r.status_code for r in results) == [201, 409]
    assert (await api.get(f"/cards/{family['card']['id']}")).json()["available_limit"] == "500.00"


async def test_concurrent_partial_payments_and_overpayment_are_atomic(api, family):
    buy = await family["write"](
        f"/cards/{family['card']['id']}/purchases", purchase(family, count=1)
    )
    path = f"/invoices/{buy['installments'][0]['invoice_id']}/payments"
    body = {"account_id": family["account"]["id"], "amount": "800.00", "payment_date": "2026-09-05"}
    results = await asyncio.gather(
        *[api.post(path, json=body, headers={"Idempotency-Key": str(uuid4())}) for _ in range(2)]
    )
    assert sorted(r.status_code for r in results) == [201, 409]
    inv = (await api.get(path.removesuffix("/payments"))).json()
    assert inv["paid"] == "800.00" and inv["outstanding"] == "400.00"
    assert (await api.get(f"/accounts/{family['account']['id']}")).json()["balance"] == "4200.00"
    assert (await report(api))["expense"] == "1200.00"


async def test_transfer_is_neutral_for_equity_and_economic_report(api, family):
    dest = await family["write"](
        "/accounts",
        {
            "name": "Poupança",
            "kind": "savings",
            "opening_balance": "0",
            "opening_date": "2026-01-01",
        },
    )
    await family["write"](
        "/transactions/transfers",
        {
            "source_account_id": family["account"]["id"],
            "destination_account_id": dest["id"],
            "amount": "1000.00",
            "date": "2026-09-05",
        },
    )
    assert (await api.get("/reports/net-worth")).json()["net_worth"] == "5000.00"
    assert (await report(api))["expense"] == "0.00"
    assert (await report(api))["income"] == "0.00"


async def test_cash_accrual_partial_settlement_and_refund(api, family):
    tx = await family["write"](
        "/transactions",
        {
            "kind": "expense",
            "amount": "100.00",
            "description": "Hotel",
            "competency_date": "2026-09-03",
            "purchase_date": "2026-09-03",
            "due_date": "2026-09-10",
            "category_id": family["category"]["id"],
            "account_id": family["account"]["id"],
            "payment_method": "pix",
        },
    )
    assert (await api.get(f"/accounts/{family['account']['id']}")).json()["balance"] == "5000.00"
    await family["write"](
        f"/transactions/{tx['id']}/settlements",
        {"amount": "40.00", "account_id": family["account"]["id"], "payment_date": "2026-09-05"},
    )
    await family["write"](
        f"/transactions/{tx['id']}/refunds",
        {
            "kind": "reimbursement",
            "amount": "20.00",
            "date": "2026-09-06",
            "account_id": family["account"]["id"],
            "category_id": family["category"]["id"],
        },
    )
    assert (await report(api))["expense"] == "80.00"
    assert (await api.get(f"/accounts/{family['account']['id']}")).json()["balance"] == "4980.00"
    assert (await api.get(f"/transactions/{tx['id']}")).json()["outstanding"] == "60.00"
    bad = await api.post(
        f"/transactions/{tx['id']}/refunds",
        json={
            "kind": "reimbursement",
            "amount": "30.00",
            "date": "2026-09-06",
            "account_id": family["account"]["id"],
            "category_id": family["category"]["id"],
        },
        headers={"Idempotency-Key": str(uuid4())},
    )
    assert bad.status_code == 409


async def test_splits_no_duplicate_and_refund_bound_per_category(api, family):
    cat2 = await family["write"]("/categories", {"name": "Limpeza", "kind": "expense"})
    body = purchase(family, "100.00", 3)
    body.pop("category_id")
    body["splits"] = [
        {"category_id": family["category"]["id"], "amount": "70.00"},
        {"category_id": cat2["id"], "amount": "30.00"},
    ]
    buy = await family["write"](f"/cards/{family['card']['id']}/purchases", body)
    economic = await report(api)
    assert economic["expense"] == "100.00"
    assert {r["amount"] for r in economic["categories"]} == {"70.00", "30.00"}
    bad = await api.post(
        f"/transactions/{buy['purchase']['id']}/refunds",
        json={
            "kind": "reversal",
            "amount": "40.00",
            "date": "2026-09-06",
            "invoice_id": buy["installments"][0]["invoice_id"],
            "category_id": cat2["id"],
        },
        headers={"Idempotency-Key": str(uuid4())},
    )
    assert bad.status_code == 409


async def test_paid_card_refund_creates_credit_and_can_be_reallocated(api, family):
    buy = await family["write"](
        f"/cards/{family['card']['id']}/purchases", purchase(family, "200.00", 2)
    )
    inv1, inv2 = [x["invoice_id"] for x in buy["installments"]]
    await family["write"](
        f"/invoices/{inv1}/payments",
        {"amount": "100.00", "account_id": family["account"]["id"], "payment_date": "2026-09-05"},
    )
    await family["write"](
        f"/transactions/{buy['purchase']['id']}/refunds",
        {
            "kind": "reversal",
            "amount": "100.00",
            "date": "2026-09-06",
            "invoice_id": inv1,
            "category_id": family["category"]["id"],
        },
    )
    assert (await api.get(f"/invoices/{inv1}")).json()["credit_balance"] == "100.00"
    moved = await family["write"](
        f"/invoices/{inv1}/credit-transfers",
        {"destination_invoice_id": inv2, "amount": "100.00", "date": "2026-09-06"},
    )
    assert moved["destination"]["outstanding"] == "0.00"
    assert (await report(api))["expense"] == "100.00"
    assert (await api.get(f"/cards/{family['card']['id']}")).json()["available_limit"] == "2000.00"


async def test_recurrence_forecast_and_materialization_do_not_book_money(api, family):
    today = datetime.now(ZoneInfo("America/Sao_Paulo")).date()
    day = today + timedelta(days=5)
    rec = await family["write"](
        "/recurrences",
        {
            "name": "Energia",
            "kind": "expense",
            "amount": "100.00",
            "variable": True,
            "frequency": "monthly",
            "start_date": str(day),
            "end_date": str(day),
            "category_id": family["category"]["id"],
            "account_id": family["account"]["id"],
            "payment_method": "boleto",
        },
    )
    path = "/reports/forecast"
    before = (await api.get(path, params={"through": str(day)})).json()
    assert before["final_balance"] == "4900.00"
    await family["write"](
        f"/recurrences/{rec['id']}/materialize", {"start": str(day), "end": str(day)}, expected=200
    )
    second = await family["write"](
        f"/recurrences/{rec['id']}/materialize", {"start": str(day), "end": str(day)}, expected=200
    )
    assert second["created"] == []
    after = (await api.get(path, params={"through": str(day)})).json()
    assert after["final_balance"] == before["final_balance"]
    assert (await api.get(f"/accounts/{family['account']['id']}")).json()["balance"] == "5000.00"
    assert (await report(api))["expense"] == "0.00"


async def test_negative_budget_rollover_and_audit_before_after(api, family):
    cat = family["category"]["id"]
    for month in ["2026-08-01", "2026-09-01"]:
        await family["write"](
            "/budgets",
            {"month": month, "category_id": cat, "amount": "100.00", "rollover": True},
            method="PUT",
            expected=200,
        )
    await family["write"](
        "/transactions",
        {
            "kind": "expense",
            "amount": "150.00",
            "description": "Excesso",
            "competency_date": "2026-08-10",
            "purchase_date": "2026-08-10",
            "due_date": "2026-08-10",
            "category_id": cat,
            "account_id": family["account"]["id"],
            "payment_method": "pix",
            "payment_date": "2026-08-10",
        },
    )
    sep = (await api.get("/budgets", params={"month": "2026-09-01"})).json()[0]
    assert sep["rollover_amount"] == "-50.00" and sep["available"] == "50.00"
    await family["write"](
        "/budgets",
        {"month": "2026-09-01", "category_id": cat, "amount": "120.00", "rollover": True},
        method="PUT",
        expected=200,
    )
    audits = (await api.get("/audit", params={"limit": 200})).json()
    entry = next(a for a in audits if a["table_name"] == "budgets" and a["action"] == "UPDATE")
    assert Decimal(str(entry["old_value"]["amount"])) == Decimal("100")
    assert Decimal(str(entry["new_value"]["amount"])) == Decimal("120")
    assert entry["actor_id"] == family["user"]["id"]


async def test_family_isolation_at_api_and_database(api, family):
    old_family = family["family"]["id"]
    other = (await api.post("/auth/families", json={"name": "Outra família"})).json()
    api.headers["X-Family-ID"] = other["id"]
    assert (await api.get(f"/accounts/{family['account']['id']}")).status_code == 404
    assert (await api.get("/accounts")).json() == []
    async with sessions()() as db:
        bypass = await db.scalar(
            text("SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname=current_user")
        )
        assert not bypass, "Testes devem usar papel da aplicação, sem BYPASSRLS/superuser."
        await db.execute(text("SELECT set_config('app.family_id', :f, true)"), {"f": other["id"]})
        assert (
            await db.scalar(select(m.Account).where(m.Account.id == family["account"]["id"]))
            is None
        )
    api.headers["X-Family-ID"] = old_family


async def test_viewer_cannot_write_and_logout_revokes_token(api, family):
    email = f"viewer-{uuid4()}@example.com"
    user = (
        await api.post(
            "/auth/register",
            json={"name": "Viewer", "email": email, "password": "viewer-password-123"},
        )
    ).json()
    await family["write"](
        "/families/members", {"user_id": user["id"], "role": "viewer"}, method="PUT", expected=200
    )
    token = (
        await api.post("/auth/token", data={"username": email, "password": "viewer-password-123"})
    ).json()["access_token"]
    api.headers["Authorization"] = "Bearer " + token
    assert (await api.get("/accounts")).status_code == 200
    denied = await api.post(
        "/categories",
        json={"name": "Test", "kind": "expense"},
        headers={"Idempotency-Key": str(uuid4())},
    )
    assert denied.status_code == 403
    assert (await api.post("/auth/logout-all")).status_code == 200
    assert (await api.get("/accounts")).status_code == 401


async def test_goal_is_earmark_not_asset_duplication(api, family):
    goal = await family["write"](
        "/goals",
        {
            "name": "Reserva",
            "account_id": family["account"]["id"],
            "target_amount": "10000.00",
            "target_date": "2028-12-31",
            "monthly_contribution": "500.00",
        },
    )
    updated = await family["write"](
        f"/goals/{goal['id']}/contributions", {"amount": "4000.00", "date": "2026-09-06"}
    )
    assert updated["current_amount"] == "4000.00"
    assert (await api.get("/reports/net-worth")).json()["net_worth"] == "5000.00"


async def test_asset_and_liability_valuations_are_not_operating_income(api, family):
    await family["write"](
        "/positions",
        {"name": "Veículo", "kind": "asset", "value": "30000.00", "date": "2026-09-01"},
    )
    await family["write"](
        "/positions",
        {
            "name": "Financiamento externo",
            "kind": "liability",
            "value": "10000.00",
            "date": "2026-09-01",
        },
    )
    assert (await api.get("/reports/net-worth")).json()["net_worth"] == "25000.00"
    assert (await report(api))["income"] == "0.00"


async def test_database_rejects_journal_mutation_and_missing_counterpart(api, family):
    async with sessions()() as db:
        await db.execute(
            text("SELECT set_config('app.family_id', :f, true)"), {"f": family["family"]["id"]}
        )
        with pytest.raises(DBAPIError):
            await db.execute(
                text("UPDATE journal_lines SET debit=debit+1 WHERE family_id=:f"),
                {"f": family["family"]["id"]},
            )
        await db.rollback()
    async with sessions()() as db:
        await db.execute(
            text(
                "SELECT set_config('app.family_id', :f, true), set_config('app.actor_id', :a, true)"
            ),
            {"f": family["family"]["id"], "a": family["user"]["id"]},
        )
        tx = await db.scalar(
            select(m.Transaction).where(m.Transaction.family_id == family["family"]["id"]).limit(1)
        )
        db.add(
            m.Journal(
                family_id=tx.family_id,
                transaction_id=tx.id,
                event="bad_test",
                economic_date=tx.competency_date,
                effective_date=tx.competency_date,
            )
        )
        await db.flush()
        with pytest.raises(DBAPIError):
            await db.commit()


async def test_insufficient_funds_rolls_back_invoice_payment(api, family):
    empty = await family["write"](
        "/accounts", {"name": "Vazia", "kind": "checking", "opening_date": "2026-01-01"}
    )
    buy = await family["write"](
        f"/cards/{family['card']['id']}/purchases", purchase(family, "100.00", 1)
    )
    inv = buy["installments"][0]["invoice_id"]
    response = await api.post(
        f"/invoices/{inv}/payments",
        json={"account_id": empty["id"], "amount": "100.00", "payment_date": "2026-09-05"},
        headers={"Idempotency-Key": str(uuid4())},
    )
    assert response.status_code == 409
    assert (await api.get(f"/invoices/{inv}")).json()["paid"] == "0.00"
