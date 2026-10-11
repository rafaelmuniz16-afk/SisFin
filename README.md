# SisFin — API de controle financeiro familiar

**Versão 1.1 — interface web e deploy Neon/Render.** Abra `/` para usar a SPA.
O guia [DEPLOY_RENDER_NEON.md](DEPLOY_RENDER_NEON.md) contém a configuração do
banco, as variáveis de ambiente, os comandos do Render e os limites da validação.
O arquivo `index.html` também pode ser hospedado separadamente; configure a URL
da API pelo botão de conexão da interface.

Implementação de referência em **Python 3.12, FastAPI, Pydantic V2, PostgreSQL 16, SQLAlchemy 2.0 assíncrono, asyncpg, Alembic, JWT e bcrypt**.

A compra reconhece a despesa e a obrigação; o pagamento da obrigação movimenta o caixa. O razão de partidas dobradas é a fonte dos saldos e relatórios. Compras parceladas têm uma raiz econômica, parcelas vinculadas a faturas e rateios independentes por categoria.

## Conteúdo

| Entrega | Arquivo |
|---|---|
| Arquitetura, ERD, dicionário e regras | `docs/ARQUITETURA.md` |
| DDL PostgreSQL 16 completo | `docs/schema.sql` |
| Models tipados | `app/models.py` |
| Compra parcelada e pagamento de fatura | `app/services/finance.py` |
| Recorrências, orçamentos, metas e relatórios | `app/services/planning.py` |
| OpenAPI exportado | `docs/openapi.json` |
| Migração inicial congelada | `alembic/versions/0001_initial.py` + `schema_v1.json` |
| Testes, resultados e limites da validação | `tests/` + `docs/VALIDACAO.md` |
| Conciliação entre cartão e razão | `docs/reconciliation.sql` |

## Executar com Docker

Pré-requisitos: Docker Engine e Compose v2. Os comandos abaixo partem da pasta que contém este README.

```bash
python3 scripts/init_env.py
docker compose up --build -d
```

O primeiro comando cria segredos locais em `.env` e recusa sobrescrever um arquivo existente. A imagem PostgreSQL cria um usuário separado para a API, sem superusuário nem `BYPASSRLS`. O serviço `migrate` aplica o Alembic antes de iniciar a API. O banco não publica porta externa; a API fica vinculada ao localhost.

- Swagger: http://localhost:8000/docs
- OpenAPI: http://localhost:8000/openapi.json
- Healthcheck da aplicação: http://localhost:8000/health

`/health` informa que a aplicação responde; não é uma checagem da conexão com o banco. O Compose verifica a prontidão do PostgreSQL separadamente.

## Primeiro uso

1. `POST /auth/register`: nome, e-mail e senha com ao menos 12 caracteres e até 72 bytes UTF-8.
2. `POST /auth/token`: formulário `application/x-www-form-urlencoded`, usando e-mail em `username` e a senha em `password`.
3. `POST /auth/families`: criar família com `Authorization: Bearer TOKEN`.
4. Usar o ID retornado como `X-Family-ID` em todas as rotas de domínio.
5. Criar categorias, contas e cartões; depois registrar as movimentações.

Todas as escritas de domínio exigem um `Idempotency-Key` de 8 a 128 caracteres. Use UUIDs. Para repetir a mesma tentativa, mantenha a chave e o corpo. Para uma operação nova, use uma nova chave. Cadastro, login, criação de família e revogação de tokens não usam essa chave.

Exemplo de compra em 3 parcelas, após criar cartão e categoria:

```http
POST /cards/CARD_UUID/purchases
Authorization: Bearer TOKEN
X-Family-ID: FAMILY_UUID
Idempotency-Key: 9a626487-54e4-4da3-b54f-c91d0fba6b9b
Content-Type: application/json

{
  "amount": "100.00",
  "installments": 3,
  "description": "Compra de exemplo",
  "competency_date": "2026-09-03",
  "purchase_date": "2026-09-03",
  "category_id": "CATEGORY_UUID"
}
```

Valores monetários devem ser enviados como **strings decimais**, por exemplo `"100.00"`. UUIDs ilustrativos precisam ser substituídos pelos IDs retornados pela API. O rateio é `33.34 + 33.33 + 33.33`, e o consumo inicial do limite é `100.00`.

```http
POST /invoices/INVOICE_UUID/payments
Authorization: Bearer TOKEN
X-Family-ID: FAMILY_UUID
Idempotency-Key: 24f8b6bd-16e1-4d42-8a51-7d196c614ba8
Content-Type: application/json

{
  "account_id": "ACCOUNT_UUID",
  "amount": "33.34",
  "payment_date": "2026-09-05",
  "payment_method": "pix"
}
```

O pagamento retorna `kind="transfer"`, reduz o saldo bancário e a dívida do cartão, libera limite e não lança outra despesa.

## Execução local sem Docker

Use PostgreSQL 16 já disponível, com os papéis de aplicação/migração separados conforme `scripts/init-db.sh`.

```bash
python3.12 -m venv .venv
. .venv/bin/activate
pip install -r requirements.lock
export DATABASE_URL='postgresql+asyncpg://sisfin_app:SENHA@localhost:5432/sisfin'
export MIGRATION_DATABASE_URL='postgresql+asyncpg://sisfin_migrator:SENHA@localhost:5432/sisfin'
export JWT_SECRET='SEGREDO_ALEATORIO_COM_PELO_MENOS_32_CARACTERES'
alembic upgrade head
uvicorn app.main:app --reload
```

`MIGRATION_DATABASE_URL` só é necessário para o Alembic. O runtime lê `DATABASE_URL`. O Alembic lê variáveis do processo; para a execução local, exporte-as como no exemplo. Não use o papel de migração para servir a API.

## Testes

Sem PostgreSQL, os testes de domínio e contrato funcionam; os de integração são explicitamente marcados como ignorados.

```bash
python -m pytest -q -m 'not integration'
ruff check app scripts tests
```

Para testar com PostgreSQL 16 em um projeto Docker separado, sem misturar os dados de uso diário:

```bash
docker compose -p sisfin-validation --profile test run --build --rm tests
```

Ou aponte `TEST_DATABASE_URL` para um **banco de teste já migrado**, com o papel da aplicação:

```bash
export TEST_DATABASE_URL='postgresql+asyncpg://sisfin_app:SENHA@localhost:5432/sisfin_test'
python -m pytest -q -m integration
```

Os testes criam usuários e famílias próprios e não truncam tabelas. Há testes de RLS que falham deliberadamente se executados como superusuário. Datas dos cenários de aceitação estão em agosto/setembro de 2026; o relógio de execução precisa ser posterior a esse período.

## Escopo

Esta entrega implementa a API e o núcleo financeiro solicitado. Inclui extensão para família, categorias, auditoria, recorrências, metas e patrimônio. Integração bancária, UI, notificações, importação OFX/CSV, câmbio, cotação de investimentos, amortização de empréstimos e autenticação multifator do esboço amplo são evoluções; não são anunciadas como recursos implementados.

O resultado da validação disponível está em `docs/VALIDACAO.md`. A execução dos testes PostgreSQL é a pendência técnica antes de considerar a versão homologada.
