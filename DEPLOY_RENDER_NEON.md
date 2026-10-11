# SisFin 1.1 — interface e publicação no Neon/Render

O pacote contém a aplicação completa e a SPA de arquivo único. O banco permanece
no Neon; o Web Service Python do Render serve a API e o `index.html` em `/`.
Também é possível servir esse HTML em uma hospedagem estática separada.
Não há etapa de build JavaScript nem dependência de Node para executar a aplicação.

## Arquivos desta etapa

| Arquivo | Função |
|---|---|
| `app/main.py` | CORS, roteadores existentes, listagem das famílias autorizadas, tratamento de indisponibilidade e HTML em `/` |
| `app/db.py` | URL `postgres://` / `postgresql://` / `postgresql+asyncpg://`, TLS e pool assíncrono |
| `alembic/env.py` | Mesma normalização e conexão direta para migrações |
| `index.html` | Interface responsiva completa, com CSS e JavaScript próprios embutidos |
| `.python-version` | Python 3.12.14, versão usada na verificação desta entrega |
| `tests/test_web_deploy.py` | Testes de URL, TLS, CORS, HTML e famílias |
| `tests/frontend/check.cjs` | Verificações funcionais do DOM e das requisições, com API simulada |

Se atualizar o projeto anterior, copie **também `app/db.py` e `alembic/env.py`**.
A normalização pertence à camada de conexão e é chamada tanto pela API quanto
pelo Alembic. Copiar apenas `main.py` não atualiza o driver do projeto anterior.
As tabelas e a revisão Alembic `0001` permanecem iguais.

## 1. Preparar o Neon

Crie o projeto com PostgreSQL **16**, escolha a região e copie o hostname, o nome
do banco e as credenciais. Use a conexão **direta**, sem `-pooler`, para as migrações.
Para uma única instância gratuita do Render, a conexão direta também atende à API:
ela usa um pool pequeno, de três conexões e até duas adicionais.

Use dois papéis: o proprietário do banco para migrar e `sisfin_app` para a API.
O papel da aplicação deve ser criado por SQL, sem a associação administrativa
`neon_superuser`. Os papéis criados pelo painel/API do Neon recebem essa associação
automaticamente, o que não atende ao isolamento por RLS esperado pelo SisFin.

No SQL Editor do Neon, conectado como proprietário **do banco do SisFin**, substitua
a senha ilustrativa abaixo por uma senha aleatória própria e execute uma vez:

```sql
CREATE ROLE sisfin_app WITH LOGIN
  PASSWORD '<SENHA_ALEATORIA_DA_APLICACAO>'
  NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE;

GRANT USAGE ON SCHEMA public TO sisfin_app;

-- O mesmo proprietário deve executar o Alembic.
ALTER DEFAULT PRIVILEGES IN SCHEMA public
  GRANT SELECT, INSERT, UPDATE ON TABLES TO sisfin_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
  GRANT USAGE, SELECT ON SEQUENCES TO sisfin_app;
```

Se as tabelas já foram criadas, execute também:

```sql
GRANT SELECT, INSERT, UPDATE ON ALL TABLES IN SCHEMA public TO sisfin_app;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO sisfin_app;
```

Não adicione `sisfin_app` ao papel `neon_superuser`. Confirme o isolamento com
os testes de integração em um banco de testes, antes de inserir dados reais.

## 2. URLs e variáveis

Configure somente no servidor ou no terminal local:

```dotenv
DATABASE_URL=postgresql://sisfin_app:SENHA_URL_ENCODED@HOST_DIRETO.neon.tech/neondb?sslmode=require
MIGRATION_DATABASE_URL=postgresql://neondb_owner:SENHA_URL_ENCODED@HOST_DIRETO.neon.tech/neondb?sslmode=require
JWT_SECRET=SEGREDO_ALEATORIO_DE_PELO_MENOS_32_CARACTERES
JWT_ISSUER=sisfin
JWT_AUDIENCE=sisfin-api
JWT_MINUTES=30
```

Substitua hostname, banco e usuários pelos valores do projeto. Senhas com
caracteres reservados de URL precisam de percent-encoding; não codifique a URL
inteira. O prefixo `postgres://` também é aceito, e ambos são convertidos para
`postgresql+asyncpg://`.

Gere o segredo JWT no seu próprio terminal:

```bash
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

`sslmode=require` é transformado em `connect_args["ssl"]` com um `SSLContext`
que valida certificado e hostname. A opção não é repassada como argumento
`sslmode` ao asyncpg. Um host Neon sem opção explícita também usa TLS validado.
TLS desabilitado é recusado para hosts Neon.

**URL com `channel_binding=require`:** esse parâmetro não é suportado por esta
integração com asyncpg. O código recusa a configuração com uma mensagem clara,
em vez de descartar a exigência silenciosamente. Para esta stack, configure
conscientemente uma URL compatível com asyncpg, usando `sslmode=require` e sem
`channel_binding`. Se channel binding for um requisito do seu ambiente, é
necessário escolher um driver compatível antes da publicação.

A API também reconhece hostname Neon com `-pooler.`: nesse caso usa `NullPool`,
desativa caches locais de statements e gera nomes de prepared statements únicos.
O Alembic exige `MIGRATION_DATABASE_URL` direto. A conexão real com o pooler não
foi exercitada nesta entrega.

## 3. Aplicar migrações

Na raiz do projeto, com Python 3.12 e PostgreSQL/Neon acessível:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.lock
```

Exporte `DATABASE_URL`, `MIGRATION_DATABASE_URL` e `JWT_SECRET` para esse terminal
com os valores reais; o Alembic lê as URLs do ambiente. Depois:

```bash
alembic upgrade head
alembic current
```

Execute essa etapa antes do primeiro deploy e quando houver novas migrações.
Não rode o DDL de `docs/schema.sql` sobre as mesmas tabelas: esse arquivo é
referência, e o Alembic controla a instalação.

O fluxo escolhido executa migrações localmente ou em uma etapa de CI autorizada.
Assim, o Web Service da API não precisa guardar a credencial administrativa
`MIGRATION_DATABASE_URL`, nem depende de shell ou pre-deploy command do plano pago.

Para testar localmente:

```bash
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

Abra `http://localhost:8000/`. A SPA usa essa origem automaticamente.

## 4. Criar o Web Service gratuito no Render

Publique os arquivos do pacote em seu repositório Git e conecte-o ao Render.
Na criação do **Web Service**, configure:

| Campo | Valor |
|---|---|
| Runtime | Python 3 |
| Plano | Free |
| Root Directory | Pasta contendo `app/`, `index.html`, `requirements.lock` e `.python-version` |
| Build Command | `pip install -r requirements.lock` |
| Start Command | `uvicorn app.main:app --host 0.0.0.0 --port $PORT --workers 1` |
| Health Check Path | `/health` |

No ambiente do Render, configure `DATABASE_URL`, `JWT_SECRET`, `JWT_ISSUER`,
`JWT_AUDIENCE` e `JWT_MINUTES`. A versão Python é definida pelo arquivo
`.python-version`; remova um eventual `PYTHON_VERSION` conflitante no painel.
Não inclua `.env` com segredos no repositório.

Depois que o deploy concluir:

- `https://SEU-SERVICO.onrender.com/`: interface;
- `https://SEU-SERVICO.onrender.com/docs`: OpenAPI/Swagger;
- `https://SEU-SERVICO.onrender.com/health`: resposta da aplicação.

O healthcheck confirma que a API responde, não que uma consulta ao Neon foi
concluída. Login, leitura de contas e uma operação financeira em ambiente de
teste verificam a conexão efetiva.

O plano gratuito entra em repouso após inatividade e pode levar aproximadamente
um minuto para retomar. A SPA aceita espera de até 90 segundos, informa a demora
e não reenvia escritas automaticamente. Na mesma origem, o Render pode mostrar
sua própria página de carregamento antes de entregar o HTML.
O plano gratuito tem limites de uso e o Render não o recomenda para aplicações
de produção; essa escolha não oferece disponibilidade contínua garantida.

## 5. Interface separada, CORS e sessão

O `index.html` pode ser hospedado sozinho. Use **Conexão / Configurar URL da API**
no login ou nas preferências e informe a raiz HTTPS do Render. A URL fica em
`localStorage["sisfin.apiBaseUrl"]`. Alternativamente, altere `DEFAULT_API_BASE`
no início do script. Ao servir em uma origem diferente, configure a API antes
de entrar. Use HTTP via localhost no desenvolvimento e HTTPS na publicação.

A API configura:

```python
allow_origins=["*"]
allow_credentials=True
allow_methods=["*"]
allow_headers=["*"]
```

A SPA usa Bearer explícito e `fetch(..., credentials="omit")`, sem cookies.
Isso evita a incompatibilidade de respostas wildcard com requisições feitas
com `credentials="include"`. Se adotar autenticação por cookies futuramente,
defina uma lista explícita de origens.

| Dado | Comportamento |
|---|---|
| JWT | Salvo em `localStorage["sisfin.token"]`; enviado como `Authorization: Bearer ...` |
| Família | Salva em `localStorage["sisfin.familyId"]`; enviada como `X-Family-ID` quando selecionada |
| Login | `POST /auth/token`, formulário `username` + `password` |
| Cadastro | `POST /auth/register`, JSON com `name`, `email`, `password` |
| Famílias | `GET /auth/families` lista apenas as participações do usuário; `POST` cria a família |
| Idempotência | UUID v4 por operação de escrita; a tentativa financeira mantém a chave após falha sem confirmação |
| Mudança da URL da API | Encerra a sessão local para impedir o envio do JWT antigo a outro servidor |
| Expiração / 401 | Limpa dados da tela e solicita novo login |
| Logout local | Remove JWT, família e dados da tela |

Login, cadastro e teste público de conexão não podem exigir um JWT ainda não
obtido; a seleção de família acontece depois de autenticar. Os cabeçalhos são
enviados assim que seus valores existem. O teste de outra URL não envia credenciais.
Não há refresh token: o usuário entra novamente ao expirar o JWT.

A SPA envia `Idempotency-Key` também aos POSTs de autenticação, porém o backend
anterior só garante deduplicação nas escritas de domínio. Se a criação de uma
família ficar sem confirmação, use **Atualizar lista de famílias** antes de
reenviar. Para operações financeiras, o navegador conserva apenas um digest e
a chave pendente no `sessionStorage`, nunca a senha ou o corpo financeiro.
Editar o formulário muda a operação e gera outra chave.

## 6. Fluxos implementados

1. Cadastre o usuário, crie ou selecione a família.
2. Adicione uma conta, uma categoria e, se necessário, um cartão.
3. Registre receitas ou despesas no PIX, débito, boleto ou dinheiro.
4. Escolha crédito para compra à vista ou parcelada em até 600 vezes.
5. Pague a fatura, integral ou parcialmente, a partir de uma conta real.
6. Consulte extrato, saldos, limites, faturas futuras e despesas por categoria.

O visualizador tem os botões de escrita desabilitados; a autorização efetiva
continua na API. Nomes, descrições e mensagens da API são tratados como texto,
sem inserir HTML do usuário.

**Dinheiro:** os formulários aceitam `150,00` ou `150.00`, mostram reais e enviam
`"150.00"`. Os cálculos usam `BigInt` em centavos. Uma compra de R$ 100,01 em
três vezes mostra R$ 33,34 + R$ 33,34 + R$ 33,33. O backend decide as faturas
e as datas do ciclo. `Number` é usado somente na geometria visual do gráfico
e da barra de limite.

**Dashboard:** o saldo é o saldo atual de todas as contas carregadas; as despesas
são o resultado por competência no mês selecionado. Faturas a pagar considera
os vencimentos daquele mês; as anteriores vencidas são indicadas e as futuras
aparecem em Contas e cartões. Valores negativos por categoria, resultantes de
estornos, são exibidos separadamente, sem criar fatias negativas no gráfico.
Uma falha de leitura não é interpretada como saldo zero.

**Liquidação:** o formulário de fatura chama apenas
`POST /invoices/{id}/payments`. A API registra transferência/baixa do passivo;
a SPA não cria uma nova despesa. O extrato identifica as liquidações separadamente.

Tailwind CSS 2.2.19 é carregado como stylesheet pré-compilado, sem o compilador
Play CDN. Lucide 0.468.0 e Chart.js 4.4.7 também usam CDN com versões fixadas.
Os três recursos têm hashes SRI verificados nesta entrega. O gráfico mantém
uma legenda textual se Chart.js não estiver disponível. A interface requer
navegador moderno com `BigInt`, `crypto.randomUUID`, Web Crypto e `<dialog>`.

## 7. Validação realizada e verificação no ambiente real

- Python 3.12.14: **55 testes passaram**; **16 testes de integração PostgreSQL**
  foram ignorados por ausência de `TEST_DATABASE_URL`.
- JavaScript: sintaxe verificada com Node.
- DOM e contratos de requisição: **11 cenários passaram** com jsdom e API simulada.
  Incluem arredondamento de parcelas, login/família, valor decimal como string,
  repetição idempotente, liquidação parcial, PIX, troca de servidor, visualizador,
  sessão expirada, paginação dos saldos e conteúdo escapado.
- Os três arquivos das CDNs responderam HTTP 200 e seus hashes foram calculados.
- Não houve deploy, acesso a credenciais Neon/Render, teste de conexão PostgreSQL
  real ou inspeção visual em navegador real nesta execução.

Para reproduzir os testes Python:

```bash
pytest -q
```

Para os testes da SPA (somente desenvolvimento; não é requisito do Render):

```bash
cd tests/frontend
npm ci
npm test
```

Antes de usar com dados reais, execute os testes de integração contra um
PostgreSQL 16 de testes migrado, com o papel restrito:

```bash
TEST_DATABASE_URL='postgresql://sisfin_app:SENHA@HOST.neon.tech/DB_TESTE?sslmode=require' pytest -m integration -q
```

Esse comando grava dados de teste; use um banco/branch dedicado. Faça também
a conferência visual no celular e no desktop e confirme o fluxo completo
compra → fatura → pagamento com os serviços publicados.

## Referências oficiais

- [Render: FastAPI](https://render.com/docs/deploy-fastapi)
- [Render: plano gratuito](https://render.com/docs/free)
- [Render: versão Python](https://render.com/docs/python-version)
- [Neon: papéis e privilégios](https://github.com/neondatabase/website/blob/main/content/docs/manage/roles.md)
- [SQLAlchemy 2.0: PostgreSQL/asyncpg](https://docs.sqlalchemy.org/en/20/dialects/postgresql.html#module-sqlalchemy.dialects.postgresql.asyncpg)
- [asyncpg: conexão e SSL](https://magicstack.github.io/asyncpg/current/api/index.html#connection)
