from datetime import date

from fastapi import APIRouter, HTTPException

from app.deps import ReadContext
from app.services import planning
from app.services.common import public

router = APIRouter(prefix="/reports", tags=["reports"])


def validate_range(start, end):
    if not 0 <= (end - start).days <= 1096:
        raise HTTPException(422, "Período inválido ou superior a 1.096 dias.")


@router.get("/economic")
async def economic(ctx: ReadContext, start: date, end: date):
    validate_range(start, end)
    return public(await planning.economic_report(ctx, start, end))


@router.get("/cash-flow")
async def cash(ctx: ReadContext, start: date, end: date):
    validate_range(start, end)
    return public(await planning.cash_report(ctx, start, end))


@router.get("/net-worth")
async def net_worth(ctx: ReadContext, at: date | None = None):
    return public(await planning.net_worth(ctx, at or ctx.today))


@router.get("/forecast")
async def forecast(ctx: ReadContext, through: date):
    return public(await planning.forecast(ctx, through))
