from functools import lru_cache

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    database_url: str
    jwt_secret: SecretStr = Field(min_length=32)
    jwt_issuer: str = "sisfin"
    jwt_audience: str = "sisfin-api"
    jwt_minutes: int = Field(default=30, ge=5, le=120)
    bcrypt_rounds: int = Field(default=12, ge=12, le=16)


@lru_cache
def settings() -> Settings:
    return Settings()
