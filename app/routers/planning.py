from uuid import UUID

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import func, select

from app import models as m
from app import schemas as s
from app.deps import AdminContext, IdempotencyKey, ReadContext, WriteContext
from app.domain import Role
from app.services import planning
from app.services.common import add, atomic, get, public
from app.services.finance import category

router = APIRouter(tags=["planning"])


@router.get("/categories", tags=["categories"])
async def categories(
    ctx: ReadContext, limit: int = Query(100, ge=1, le=200), offset: int = Query(0, ge=0)
):
    return public(
        list(
            await ctx.db.scalars(
                select(m.Category)
                .where(m.Category.family_id == ctx.family_id)
                .order_by(m.Category.name, m.Category.id)
                .limit(limit)
                .offset(offset)
            )
        )
    )


@router.post("/categories", tags=["categories"], status_code=201)
async def category_create(payload: s.CategoryCreate, ctx: WriteContext, key: IdempotencyKey):
    async def action():
        if payload.parent_id:
            await category(ctx, payload.parent_id, payload.kind)
        return await add(ctx, m.Category, **payload.model_dump())

    return await atomic(ctx, key, "category.create", payload, action)


@router.get("/recurrences")
async def recurrences(
    ctx: ReadContext, limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0)
):
    return public(
        list(
            await ctx.db.scalars(
                select(m.Recurrence)
                .where(m.Recurrence.family_id == ctx.family_id)
                .order_by(m.Recurrence.id)
                .limit(limit)
                .offset(offset)
            )
        )
    )


@router.post("/recurrences", status_code=201)
async def recurrence_create(payload: s.RecurrenceCreate, ctx: WriteContext, key: IdempotencyKey):
    return await atomic(
        ctx, key, "recurrence.create", payload, lambda: planning.create_recurrence(ctx, payload)
    )


@router.post("/recurrences/{recurrence_id}/materialize")
async def materialize(
    recurrence_id: UUID, payload: s.DateRange, ctx: WriteContext, key: IdempotencyKey
):
    return await atomic(
        ctx,
        key,
        f"recurrence.materialize:{recurrence_id}",
        payload,
        lambda: planning.materialize(ctx, recurrence_id, payload),
    )


@router.post("/recurrences/{recurrence_id}/deactivate")
async def deactivate(recurrence_id: UUID, ctx: WriteContext, key: IdempotencyKey):
    async def action():
        rec = await get(ctx, m.Recurrence, recurrence_id, lock=True)
        rec.active = False
        await ctx.db.flush()
        return rec

    return await atomic(ctx, key, f"recurrence.deactivate:{recurrence_id}", {}, action)


@router.get("/goals")
async def goals(
    ctx: ReadContext, limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0)
):
    rows = await ctx.db.scalars(
        select(m.Goal)
        .where(m.Goal.family_id == ctx.family_id)
        .order_by(m.Goal.id)
        .limit(limit)
        .offset(offset)
    )
    return public([await planning.goal_view(ctx, row) for row in rows])


@router.post("/goals", status_code=201)
async def goal_create(payload: s.GoalCreate, ctx: WriteContext, key: IdempotencyKey):
    async def action():
        await get(ctx, m.Account, payload.account_id)
        goal = await add(ctx, m.Goal, **payload.model_dump())
        return await planning.goal_view(ctx, goal)

    return await atomic(ctx, key, "goal.create", payload, action)


@router.post("/goals/{goal_id}/contributions", status_code=201)
async def contribute(
    goal_id: UUID, payload: s.Contribution, ctx: WriteContext, key: IdempotencyKey
):
    return await atomic(
        ctx,
        key,
        f"goal.contribute:{goal_id}",
        payload,
        lambda: planning.contribute(ctx, goal_id, payload),
    )


@router.get("/positions")
async def positions(
    ctx: ReadContext, limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0)
):
    return public(
        list(
            await ctx.db.scalars(
                select(m.WealthPosition)
                .where(m.WealthPosition.family_id == ctx.family_id)
                .order_by(m.WealthPosition.id)
                .limit(limit)
                .offset(offset)
            )
        )
    )


@router.post("/positions", status_code=201)
async def position_create(payload: s.PositionCreate, ctx: WriteContext, key: IdempotencyKey):
    return await atomic(
        ctx, key, "position.create", payload, lambda: planning.create_position(ctx, payload)
    )


@router.post("/positions/{position_id}/valuations", status_code=201)
async def valuation(
    position_id: UUID, payload: s.Valuation, ctx: WriteContext, key: IdempotencyKey
):
    return await atomic(
        ctx,
        key,
        f"position.value:{position_id}",
        payload,
        lambda: planning.value_position(ctx, position_id, payload),
    )


@router.get("/families/members", tags=["families"])
async def members(ctx: ReadContext):
    return public(
        list(
            await ctx.db.scalars(
                select(m.FamilyMember).where(m.FamilyMember.family_id == ctx.family_id)
            )
        )
    )


@router.put("/families/members", tags=["families"])
async def put_member(payload: s.MemberInput, ctx: AdminContext, key: IdempotencyKey):
    async def action():
        if not await ctx.db.get(m.User, payload.user_id):
            raise HTTPException(
                404, "Usuário não encontrado; informe o ID de um usuário já cadastrado."
            )
        member = await ctx.db.get(m.FamilyMember, (ctx.family_id, payload.user_id))
        if member:
            if member.role == Role.ADMIN and payload.role != Role.ADMIN:
                admins = await ctx.db.scalar(
                    select(func.count())
                    .select_from(m.FamilyMember)
                    .where(
                        m.FamilyMember.family_id == ctx.family_id, m.FamilyMember.role == Role.ADMIN
                    )
                )
                if admins <= 1:
                    raise HTTPException(409, "Família deve manter pelo menos um administrador.")
            member.role = payload.role
        else:
            member = m.FamilyMember(family_id=ctx.family_id, **payload.model_dump())
            ctx.db.add(member)
        await ctx.db.flush()
        return member

    return await atomic(ctx, key, "member.upsert", payload, action)


@router.get("/audit", tags=["audit"])
async def audit(
    ctx: AdminContext, limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0)
):
    return public(
        list(
            await ctx.db.scalars(
                select(m.AuditLog)
                .where(m.AuditLog.family_id == ctx.family_id)
                .order_by(m.AuditLog.created_at.desc(), m.AuditLog.id)
                .limit(limit)
                .offset(offset)
            )
        )
    )
