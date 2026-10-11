from dataclasses import dataclass
from datetime import datetime
from typing import Annotated
from uuid import UUID
from zoneinfo import ZoneInfo

import jwt
from fastapi import Depends, Header, HTTPException
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.domain import Role
from app.models import Family, FamilyMember, User
from app.security import decode_token

DB = Annotated[AsyncSession, Depends(get_db)]
oauth2 = OAuth2PasswordBearer(tokenUrl="/auth/token")


async def current_user(db: DB, token: Annotated[str, Depends(oauth2)]) -> User:
    invalid = HTTPException(
        401, "Credenciais inválidas ou expiradas.", headers={"WWW-Authenticate": "Bearer"}
    )
    try:
        claims = decode_token(token)
        user_id = UUID(claims["sub"])
    except (jwt.PyJWTError, ValueError, KeyError):
        raise invalid
    user = await db.get(User, user_id)
    if not user or not user.active or user.token_version != claims["ver"]:
        raise invalid
    return user


CurrentUser = Annotated[User, Depends(current_user)]


@dataclass
class Context:
    db: AsyncSession
    family_id: UUID
    actor_id: UUID
    role: Role
    timezone: str

    @property
    def today(self):
        return datetime.now(ZoneInfo(self.timezone)).date()


async def context(
    db: DB, user: CurrentUser, family_id: Annotated[UUID, Header(alias="X-Family-ID")]
) -> Context:
    member = await db.get(FamilyMember, (family_id, user.id))
    if not member:
        raise HTTPException(403, "Você não participa desta família.")
    family = await db.get(Family, family_id)
    await db.execute(
        text(
            "SELECT set_config('app.family_id', :family, true), set_config('app.actor_id', :actor, true)"
        ),
        {"family": str(family_id), "actor": str(user.id)},
    )
    return Context(db, family_id, user.id, member.role, family.timezone)


async def editor(ctx: Annotated[Context, Depends(context)]) -> Context:
    if ctx.role not in (Role.ADMIN, Role.EDITOR):
        raise HTTPException(403, "Permissão de edição necessária.")
    return ctx


async def admin(ctx: Annotated[Context, Depends(editor)]) -> Context:
    if ctx.role != Role.ADMIN:
        raise HTTPException(403, "Permissão de administrador necessária.")
    return ctx


ReadContext = Annotated[Context, Depends(context)]
WriteContext = Annotated[Context, Depends(editor)]
AdminContext = Annotated[Context, Depends(admin)]
IdempotencyKey = Annotated[str, Header(alias="Idempotency-Key", min_length=8, max_length=128)]
