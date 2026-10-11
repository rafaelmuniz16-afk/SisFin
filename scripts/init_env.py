"""Configuração local: gera segredos, preserva .env existente, não imprime senhas."""

from pathlib import Path
from secrets import token_hex

target = Path(__file__).resolve().parents[1] / ".env"
content = (
    f"DB_MIGRATION_PASSWORD={token_hex(24)}\n"
    f"DB_APP_PASSWORD={token_hex(24)}\n"
    f"JWT_SECRET={token_hex(48)}\n"
)
with target.open("x") as handle:
    handle.write(content)
target.chmod(0o600)
print("Configuração .env criada. Um arquivo existente nunca é sobrescrito.")
