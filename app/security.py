from datetime import datetime, timedelta, timezone
from uuid import uuid4

import bcrypt
import jwt
from starlette.concurrency import run_in_threadpool

from app.config import settings


async def hash_password(value: str) -> str:
    return (
        await run_in_threadpool(
            bcrypt.hashpw, value.encode(), bcrypt.gensalt(settings().bcrypt_rounds)
        )
    ).decode()


async def verify_password(value: str, hashed: str) -> bool:
    if len(value.encode()) > 72:
        return False
    try:
        return await run_in_threadpool(bcrypt.checkpw, value.encode(), hashed.encode())
    except ValueError:
        return False


def access_token(user) -> str:
    now = datetime.now(timezone.utc)
    conf = settings()
    return jwt.encode(
        {
            "sub": str(user.id),
            "ver": user.token_version,
            "iat": now,
            "nbf": now,
            "exp": now + timedelta(minutes=conf.jwt_minutes),
            "jti": str(uuid4()),
            "iss": conf.jwt_issuer,
            "aud": conf.jwt_audience,
        },
        conf.jwt_secret.get_secret_value(),
        algorithm="HS256",
    )


def decode_token(token: str) -> dict:
    conf = settings()
    return jwt.decode(
        token,
        conf.jwt_secret.get_secret_value(),
        algorithms=["HS256"],
        audience=conf.jwt_audience,
        issuer=conf.jwt_issuer,
        options={"require": ["sub", "ver", "exp", "iat", "nbf", "jti", "iss", "aud"]},
    )
