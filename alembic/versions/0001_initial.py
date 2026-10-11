"""Schema e invariantes financeiros iniciais; snapshot independente dos models atuais."""

import json
from pathlib import Path
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    for statement in json.loads(Path(__file__).with_name("schema_v1.json").read_text()):
        op.execute(statement)


def downgrade():
    # Bloqueia destruição acidental de histórico contábil. Restore por backup ou
    # migration compensatória revisada; não existe DROP CASCADE silencioso.
    raise RuntimeError("Migration inicial não admite downgrade destrutivo.")
