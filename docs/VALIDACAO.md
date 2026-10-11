## Atualização 1.1 — SPA e Neon/Render

A etapa de interface adiciona 14 casos Python aos 41 anteriores: 55 passam e
16 integrações PostgreSQL permanecem sem execução por falta de banco configurado.
Há ainda 11 cenários funcionais em jsdom. Consulte `DEPLOY_RENDER_NEON.md`
para escopo e limites; as seções abaixo registram a entrega anterior.

# Validação da entrega

**Data:** 02/10/2026 (UTC) · **Runtime:** Python 3.12.14.

## Resultados executados

| Verificação | Resultado |
|---|---|
| Testes de domínio e contrato HTTP/OpenAPI | **41 aprovados** |
| Testes de integração PostgreSQL | **16 ignorados, não executados** |
| Ruff em `app`, `scripts` e `tests` | Aprovado, sem apontamentos |
| Compilação Python de aplicação, scripts e migrations | Aprovada |
| Geração do DDL pelos models SQLAlchemy/PostgreSQL | 23 tabelas, 158 instruções |
| Alembic `upgrade head --sql` | Gerado sem erro, modo offline |
| Exportação OpenAPI | 38 caminhos, 47 operações |
| Endpoint `/health` em transporte HTTP ASGI local | Aprovado |

Comando da suíte: `python -m pytest -q`.

```text
41 passed, 16 skipped
```

O ambiente de execução não disponibilizou Docker nem um servidor PostgreSQL utilizável. A tentativa de iniciar uma distribuição local do PostgreSQL foi bloqueada por permissões do ambiente. Portanto, **não foi demonstrada a execução real das migrations, dos locks, do RLS ou dos triggers no PostgreSQL 16**. Gerar SQL offline e compilar Python não substitui essa validação. Nenhuma alegação de homologação em produção é feita.

## Cobertura executada

- Conservação dos centavos, distribuição do resto e rejeição de parcela de valor zero.
- Combinações adicionais de valores e quantidades, até 600 parcelas.
- Rejeição de float, NaN, infinito, frações de centavo e estouro de `NUMERIC(12,2)`.
- Compras antes, no dia e depois do fechamento; virada de ano; fevereiro; dias 29–31.
- Frequência quinzenal de 14 dias; âncora mensal e ano bissexto sem deslocamento permanente.
- Prioridade de status de fatura, incluindo parcial vencida e saldo credor.
- Rateios com soma exata, categoria ou rateios exclusivos.
- Limite de senha bcrypt em bytes UTF-8 e preservação de espaços na senha.
- Serialização decimal e limite temporal de consultas.
- Compilação do DDL e unicidade dos nomes de constraints compostas.
- Presença dos endpoints e das exigências de Bearer, família e idempotência no OpenAPI.
- Resposta real do healthcheck por ASGI.

## Integração preparada, aguardando execução

Os 16 testes em `tests/test_integration.py` verificam:

1. Compra no crédito, limite integral, faturas futuras e pagamento sem duplicidade de despesa.
2. Parcelas com centavos e repetição concorrente da mesma chave de idempotência.
3. Duas compras concorrentes que, juntas, ultrapassariam o limite.
4. Dois pagamentos parciais concorrentes e bloqueio de pagamento excedente.
5. Transferência sem alteração de patrimônio ou resultado econômico.
6. Despesa a pagar, liquidação parcial e reembolso limitado ao desembolso.
7. Rateio por categoria sem dupla contagem e teto de devolução por categoria.
8. Estorno após pagamento e realocação de crédito entre faturas.
9. Projeção e materialização recorrentes sem movimentar caixa nem duplicar previsão.
10. Rollover negativo e auditoria com valor anterior/novo.
11. Isolamento por família na API e diretamente no banco com RLS.
12. Bloqueio de escrita para visualizador e revogação de tokens.
13. Meta como reserva, sem duplicar patrimônio.
14. Avaliação de ativos/passivos sem criar receita operacional.
15. Rejeição de edição do razão e diário sem contrapartida no commit.
16. Rollback de pagamento de fatura por insuficiência de saldo.

Para executar em banco isolado, seguindo a configuração do README:

```bash
docker compose -p sisfin-validation --profile test run --build --rm tests
```

O papel da aplicação precisa ser `NOSUPERUSER NOBYPASSRLS`. Há uma asserção que rejeita testes de isolamento rodados com privilégios capazes de ignorar RLS.

## Limites de produto explícitos

API/backend sem interface gráfica; moeda única BRL; sem automação de feriados bancários, juros de rotativo, amortização, Open Finance, cotação de ativos, MFA, recuperação de senha ou notificações. Anexos são metadados/URLs validadas; não há upload de binário. Reservas de metas não bloqueiam gastos posteriores na conta. Créditos entre faturas têm realocação explícita. O escopo e as convenções estão em `ARQUITETURA.md`.
