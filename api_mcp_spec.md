# Pet Finance — API + MCP Contract v1

## 1. Princípios

- PostgreSQL é a fonte oficial.
- Toda escrita financeira gera `audit_log`.
- Nenhuma exclusão financeira é física no MVP; usar `status=CANCELLED`.
- Valores monetários são `decimal`, nunca `float`.
- Datas financeiras usam `YYYY-MM-DD`.
- Timestamps ficam em UTC; exibição em `America/Sao_Paulo`.
- Operações de escrita aceitam `idempotency_key` para impedir duplicidade.

## 2. REST API

Base sugerida: `/api/v1`

### Contas

`GET /accounts`
- Lista contas e saldo atual.

`POST /accounts`
```json
{
  "name": "Carteira",
  "type": "CASH",
  "currency_code": "BRL",
  "opening_balance": 0
}
```

### Categorias

`GET /categories`
`POST /categories`

### Transações

`POST /transactions`

```json
{
  "type": "EXPENSE",
  "status": "POSTED",
  "description": "Gasolina",
  "amount": "50.00",
  "transaction_date": "2026-10-04",
  "account_id": "<uuid>",
  "category_id": "<uuid>",
  "tags": [],
  "source": "CHATGPT",
  "idempotency_key": "chatgpt-2026-10-04-gasolina-50-001"
}
```

Resposta:
```json
{
  "id": "<uuid>",
  "status": "POSTED",
  "duplicate_warning": false
}
```

`GET /transactions`
Filtros:
- `start_date`
- `end_date`
- `account_id`
- `category_id`
- `status`
- `type`
- `tag`
- `search`

`PATCH /transactions/{id}`

`POST /transactions/{id}/cancel`

### Recorrências

`GET /recurrences`
`POST /recurrences`
`PATCH /recurrences/{id}`

Exemplo:
```json
{
  "description": "Apple Music",
  "type": "EXPENSE",
  "expected_amount": "11.90",
  "frequency": "MONTHLY",
  "due_day": 22,
  "start_date": "2026-01-22",
  "category_id": "<uuid>",
  "account_id": "<uuid>"
}
```

### Parcelamentos

`GET /installments`
`POST /installments`

Exemplo:
```json
{
  "description": "iPhone para Sempre",
  "installment_amount": "216.33",
  "total_installments": 21,
  "first_installment_date": "2026-08-09",
  "category_id": "<uuid>",
  "account_id": "<uuid>"
}
```

### Dashboard

`GET /dashboard/monthly?year=2026&month=10`

Resposta mínima:
```json
{
  "current_balance": "-3039.75",
  "income_month": "0.00",
  "expense_month": "0.00",
  "pending_commitments": "0.00",
  "free_after_commitments": "0.00",
  "forecast_closing_balance": "0.00",
  "essential_expense": "0.00",
  "fundamental_expense": "0.00",
  "superfluous_expense": "0.00",
  "top_categories": []
}
```

### Auditoria

`GET /audit/transactions`
`GET /audit/recurrences`
`GET /audit/installments`
`GET /audit/duplicates`

### Migração CloFin

`POST /imports/clofin/preview`
- valida arquivo/JSON;
- não grava.

`POST /imports/clofin/commit`
- cria `import_batch`;
- usa `external_id`;
- deve ser idempotente.

`GET /imports/{batch_id}/reconciliation`

## 3. MCP Tools

O servidor MCP deve ser fino: validar argumentos e chamar a API de domínio.

### Escrita

`create_transaction`
- intent/type
- amount
- description
- date
- status
- account
- category
- tags
- notes

`update_transaction`

`cancel_transaction`

`create_recurrence`
`update_recurrence`

`create_installment_plan`

### Consulta

`list_transactions`
`get_transaction`
`list_accounts`
`list_categories`
`list_recurrences`
`list_installments`

### Inteligência financeira

`get_balance`

`monthly_summary`

`financial_dashboard`

`forecast_month`

`audit_transactions`

`audit_recurring_payments`

`audit_installments`

`find_duplicate_transactions`

## 4. Regra para create_transaction

Fluxo:

1. resolver conta;
2. resolver categoria;
3. gerar/validar `idempotency_key`;
4. buscar duplicidades;
5. se score >= 0.90, retornar aviso e não gravar sem `force=true`;
6. criar transação;
7. criar tags;
8. gravar audit log;
9. retornar saldo atualizado.

## 5. Detecção de duplicidade

Score inicial sugerido:

- mesmo valor: 0.40
- mesma data: 0.25
- descrição muito semelhante: até 0.20
- mesma categoria: 0.10
- mesma conta: 0.05

Faixas:
- `< 0.75`: ignorar
- `0.75–0.89`: aviso
- `>= 0.90`: bloquear por padrão

## 6. Recorrências

Job diário:

1. carregar recorrências ativas com vencimento até hoje;
2. procurar transação correspondente;
3. marcar ocorrência como:
   - `MATCHED`
   - `PENDING`
   - `OVERDUE`
   - `AMOUNT_MISMATCH`
4. gerar alertas apenas quando houver ação necessária.

## 7. Projeção

`forecast_month` considera:

`saldo atual`
`+ receitas previstas`
`- despesas recorrentes pendentes`
`- parcelas pendentes`
`- margem de segurança`

Configuração inicial recomendada:
- margem de segurança fixa ou percentual configurável.

## 8. Segurança

- API HTTPS.
- Service role nunca exposta ao cliente.
- MCP usa token próprio.
- Row Level Security se usar Supabase.
- Backups automáticos.
- Segredos apenas em `.env`.
- Logs sem dados sensíveis desnecessários.

## 9. Testes obrigatórios

Cobertura mínima para:
- criação de despesa;
- receita;
- transferência;
- cancelamento;
- duplicidade;
- recorrência;
- parcelamento;
- saldo;
- reconciliação de importação;
- idempotência.

## 10. Critério de compatibilidade com CloFin

Antes da migração definitiva:
- mesma quantidade de transações conciliadas;
- diferença total R$ 0,00;
- categorias mapeadas;
- recorrências conferidas;
- parcelamentos conferidos;
- backups restauráveis.