from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError

from app import models as m
from app import responses as out
from app import schemas as s
from app.deps import DB, Context, CurrentUser
from app.domain import Role
from app.security import access_token, hash_password, verify_password
from app.services.common import initialize_ledger, public

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/register", status_code=201)
async def register(payload: s.Register, db: DB):
    row = m.User(
        email=str(payload.email).lower(),
        name=payload.name,
        password_hash=await hash_password(payload.password),
    )
    db.add(row)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(409, "Cadastro indisponível para este e-mail.")
    return {"id": str(row.id), "name": row.name, "email": row.email}


@router.post("/token", response_model=out.TokenView)
async def token(form: Annotated[OAuth2PasswordRequestForm, Depends()], db: DB):
    user = await db.scalar(select(m.User).where(m.User.email == form.username.lower()))
    # Dummy bcrypt de mesmo custo quando o usuário não existe.
    if not user:
        await hash_password("dummy-password-not-a-user")
    if not user or not user.active or not await verify_password(form.password, user.password_hash):
        raise HTTPException(
            401, "E-mail ou senha inválidos.", headers={"WWW-Authenticate": "Bearer"}
        )
    return {"access_token": access_token(user), "token_type": "bearer"}


@router.get("/me")
async def me(user: CurrentUser, db: DB):
    memberships = list(
        await db.scalars(select(m.FamilyMember).where(m.FamilyMember.user_id == user.id))
    )
    return {
        "id": str(user.id),
        "name": user.name,
        "email": user.email,
        "families": public(memberships),
    }


@router.post("/logout-all")
async def logout_all(user: CurrentUser, db: DB):
    locked = await db.scalar(
        select(m.User)
        .where(m.User.id == user.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    locked.token_version += 1
    await db.commit()
    return {"revoked": True}


@router.post("/families", status_code=201)
async def create_family(payload: s.FamilyCreate, user: CurrentUser, db: DB):
    family = m.Family(id=uuid4(), name=payload.name, timezone=payload.timezone)
    db.add(family)
    await db.flush()
    await db.execute(
        text(
            "SELECT set_config('app.family_id', :family, true), set_config('app.actor_id', :actor, true)"
        ),
        {"family": str(family.id), "actor": str(user.id)},
    )
    db.add(m.FamilyMember(family_id=family.id, user_id=user.id, role=Role.ADMIN))
    await db.flush()
    await initialize_ledger(Context(db, family.id, user.id, Role.ADMIN, family.timezone))
    await db.commit()
    return public(family)
