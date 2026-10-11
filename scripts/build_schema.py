"""Gera o snapshot inicial. Depois de publicado, V1 é imutável; mudanças usam V2+."""

import json
from pathlib import Path

from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateIndex, CreateTable

from app.models import Base

ROOT = Path(__file__).resolve().parents[1]


def statements():
    dialect = postgresql.dialect()
    commands = [
        str(CreateTable(t).compile(dialect=dialect)).strip() for t in Base.metadata.sorted_tables
    ]
    commands += [
        str(CreateIndex(i).compile(dialect=dialect)).strip()
        for t in Base.metadata.sorted_tables
        for i in sorted(t.indexes, key=lambda i: i.name)
    ]
    commands.extend(
        [
            """CREATE FUNCTION audit_row() RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE before_value jsonb; after_value jsonb; row_value jsonb; monetary_key text;
        BEGIN
          IF TG_OP <> 'INSERT' THEN before_value := to_jsonb(OLD); END IF;
          IF TG_OP <> 'DELETE' THEN after_value := to_jsonb(NEW); END IF;
          -- Strings decimais evitam float na desserialização de JSONB no cliente.
          FOREACH monetary_key IN ARRAY ARRAY['amount','credit_limit','debit','credit','target_amount','monthly_contribution'] LOOP
            IF before_value ? monetary_key THEN
              before_value:=jsonb_set(before_value,ARRAY[monetary_key],to_jsonb(before_value->>monetary_key));
            END IF;
            IF after_value ? monetary_key THEN
              after_value:=jsonb_set(after_value,ARRAY[monetary_key],to_jsonb(after_value->>monetary_key));
            END IF;
          END LOOP;
          row_value := COALESCE(after_value, before_value);
          INSERT INTO audit_logs(id, family_id, actor_id, table_name, row_key, action, old_value, new_value, created_at)
          VALUES(gen_random_uuid(), (row_value->>'family_id')::uuid,
                 NULLIF(current_setting('app.actor_id', true), '')::uuid,
                 TG_TABLE_NAME, COALESCE(row_value->>'id', row_value->>'user_id'),
                 TG_OP, before_value, after_value, now());
          RETURN COALESCE(NEW, OLD);
        END $$""",
            """CREATE FUNCTION reject_mutation() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          RAISE EXCEPTION 'Registro imutável: %', TG_TABLE_NAME USING ERRCODE='23514';
        END $$""",
            """CREATE FUNCTION validate_journal() RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE target uuid; fam uuid; total numeric; count_lines integer;
        BEGIN
          IF TG_TABLE_NAME='journals' THEN target:=NEW.id; ELSE target:=NEW.journal_id; END IF;
          fam:=NEW.family_id;
          SELECT COALESCE(sum(debit-credit),0), count(*) INTO total,count_lines
            FROM journal_lines WHERE family_id=fam AND journal_id=target;
          IF count_lines < 2 OR total <> 0 THEN
            RAISE EXCEPTION 'Diário sem contrapartida ou desbalanceado' USING ERRCODE='23514';
          END IF;
          RETURN NULL;
        END $$""",
            """CREATE FUNCTION line_insert_guard() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          IF NOT EXISTS(SELECT 1 FROM journals j WHERE j.family_id=NEW.family_id
            AND j.id=NEW.journal_id
            AND j.xmin::text = (pg_current_xact_id()::text::numeric % 4294967296)::text) THEN
            RAISE EXCEPTION 'Não se acrescentam linhas a diário já confirmado' USING ERRCODE='23514';
          END IF;
          RETURN NEW;
        END $$""",
            """CREATE FUNCTION protect_transaction() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          IF TG_OP='DELETE' THEN
            RAISE EXCEPTION 'Transação não pode ser excluída' USING ERRCODE='23514';
          END IF;
          IF OLD.status <> 'planned' AND
            (to_jsonb(OLD)-'paid_date') IS DISTINCT FROM (to_jsonb(NEW)-'paid_date') THEN
            RAISE EXCEPTION 'Fato confirmado exige lançamento compensatório' USING ERRCODE='23514';
          END IF;
          RETURN NEW;
        END $$""",
            """CREATE FUNCTION validate_tree() RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE target uuid; fam uuid; root_row transactions%ROWTYPE; part_sum numeric;
                split_sum numeric; shares_sum numeric; installments_count integer;
        BEGIN
          fam:=NEW.family_id;
          IF TG_TABLE_NAME='transaction_shares' THEN target:=NEW.transaction_id;
          ELSE target:=COALESCE(NEW.parent_id, NEW.id); END IF;
          SELECT * INTO root_row FROM transactions WHERE family_id=fam AND id=target;
          IF root_row.node <> 'root' THEN
            RAISE EXCEPTION 'Pai deve ser transação raiz' USING ERRCODE='23514';
          END IF;
          SELECT sum(amount) FILTER(WHERE node='installment'),
                 sum(amount) FILTER(WHERE node='split'),
                 count(*) FILTER(WHERE node='installment')
            INTO part_sum,split_sum,installments_count FROM transactions
            WHERE family_id=fam AND parent_id=target;
          IF (part_sum IS NOT NULL AND part_sum <> root_row.amount)
             OR (split_sum IS NOT NULL AND split_sum <> root_row.amount) THEN
            RAISE EXCEPTION 'Soma das parcelas/rateios difere da compra' USING ERRCODE='23514';
          END IF;
          IF root_row.kind='expense' AND root_row.card_id IS NOT NULL AND root_row.status='posted'
             AND (part_sum IS NULL OR installments_count<>root_row.installment_count) THEN
            RAISE EXCEPTION 'Compra no cartão exige todas as parcelas' USING ERRCODE='23514';
          END IF;
          SELECT sum(amount) INTO shares_sum FROM transaction_shares WHERE family_id=fam AND transaction_id=target;
          IF shares_sum IS NOT NULL AND shares_sum <> root_row.amount THEN
            RAISE EXCEPTION 'Soma das participações difere da compra' USING ERRCODE='23514';
          END IF;
          RETURN NULL;
        END $$""",
            """CREATE FUNCTION validate_invoice_item() RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE tx transactions%ROWTYPE; inv invoices%ROWTYPE;
        BEGIN
          SELECT * INTO tx FROM transactions WHERE family_id=NEW.family_id AND id=NEW.transaction_id;
          SELECT * INTO inv FROM invoices WHERE family_id=NEW.family_id AND id=NEW.invoice_id;
          IF tx.card_id IS DISTINCT FROM inv.card_id THEN
            RAISE EXCEPTION 'Item e fatura devem pertencer ao mesmo cartão' USING ERRCODE='23514';
          END IF;
          RETURN NEW;
        END $$""",
            "CREATE CONSTRAINT TRIGGER journal_balanced AFTER INSERT ON journals DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION validate_journal()",
            "CREATE CONSTRAINT TRIGGER lines_balanced AFTER INSERT ON journal_lines DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION validate_journal()",
            "CREATE TRIGGER lines_insert_guard BEFORE INSERT ON journal_lines FOR EACH ROW EXECUTE FUNCTION line_insert_guard()",
            "CREATE TRIGGER protect_transaction BEFORE UPDATE OR DELETE ON transactions FOR EACH ROW EXECUTE FUNCTION protect_transaction()",
            "CREATE CONSTRAINT TRIGGER transaction_tree AFTER INSERT OR UPDATE ON transactions DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION validate_tree()",
            "CREATE CONSTRAINT TRIGGER shares_total AFTER INSERT ON transaction_shares DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION validate_tree()",
            "CREATE TRIGGER invoice_card_guard BEFORE INSERT ON invoice_items FOR EACH ROW EXECUTE FUNCTION validate_invoice_item()",
        ]
    )
    immutable = [
        "journals",
        "journal_lines",
        "invoice_items",
        "invoice_payments",
        "cash_settlements",
        "audit_logs",
        "transaction_shares",
        "attachments",
        "goal_contributions",
        "idempotency_records",
        "ledger_accounts",
    ]
    for name in immutable:
        commands.append(
            f"CREATE TRIGGER immutable_{name} BEFORE UPDATE OR DELETE ON {name} FOR EACH ROW EXECUTE FUNCTION reject_mutation()"
        )
    for table in Base.metadata.sorted_tables:
        name = table.name
        if "family_id" in table.c and name not in ("audit_logs", "idempotency_records"):
            commands.append(
                f"CREATE TRIGGER audit_{name} AFTER INSERT OR UPDATE OR DELETE ON {name} FOR EACH ROW EXECUTE FUNCTION audit_row()"
            )
        if "family_id" in table.c and name != "family_members":
            commands += [
                f"ALTER TABLE {name} ENABLE ROW LEVEL SECURITY",
                f"ALTER TABLE {name} FORCE ROW LEVEL SECURITY",
                f"CREATE POLICY family_isolation ON {name} USING (family_id = NULLIF(current_setting('app.family_id', true),'')::uuid) WITH CHECK (family_id = NULLIF(current_setting('app.family_id', true),'')::uuid)",
            ]
    return commands


if __name__ == "__main__":
    commands = statements()
    (ROOT / "alembic/versions/schema_v1.json").write_text(
        json.dumps(commands, indent=2, ensure_ascii=False) + "\n"
    )
    (ROOT / "docs/schema.sql").write_text(
        "-- PostgreSQL 16. Snapshot V1. Use Alembic OU este DDL, não os dois.\nBEGIN;\n\n"
        + ";\n\n".join(commands)
        + ";\nCOMMIT;\n"
    )
    print(f"{len(Base.metadata.tables)} tabelas; {len(commands)} instruções DDL")
