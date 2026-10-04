# Pet Finance

API financeira pessoal em Python 3.12, FastAPI, SQLAlchemy 2 e PostgreSQL.
Sprint 1: contas, categorias, transações e auditoria. O CloFin permanece a fonte
oficial até conciliação e validação da migração.

## Executar localmente

```bash
cp .env.example .env
# Edite API_TOKEN com um token longo e aleatório.
docker compose up --build
```

A API fica em http://localhost:8000/docs; `/health` consulta o banco.
Nas rotas `/api/v1`, envie `Authorization: Bearer SEU_TOKEN`.
O Compose aplica as migrações e cria um usuário local e a conta Carteira.
O volume `postgres_data` mantém os dados entre reinícios.
Credenciais do Compose são apenas para desenvolvimento local; não publique
esse ambiente na internet. A autenticação é de usuário único nesta versão.

## Primeiro lançamento

1. `GET /api/v1/accounts`: copie o `account_id` da Carteira.
2. `POST /api/v1/categories`: `{"name":"Combustível"}`; copie o `category_id`.
3. `POST /api/v1/transactions`:

```json
{
  "type": "EXPENSE",
  "status": "POSTED",
  "description": "Gasolina de teste",
  "amount": "50.00",
  "transaction_date": "2026-10-04",
  "account_id": "UUID_DA_CONTA",
  "category_id": "UUID_DA_CATEGORIA",
  "idempotency_key": "teste-gasolina-001"
}
```

4. Consulte as transações, o saldo em `/accounts` e `/audit/transactions`.
5. Cancele o teste em `POST /transactions/{id}/cancel`.

Valores são Decimal e retornam como strings. Apenas transações POSTED afetam o
saldo; cancelamento mantém o registro e reverte seu efeito. PENDING não afeta o saldo.
Não há exclusão física. Contas e categorias podem ser desativadas por PATCH.
Saldo inicial é definido apenas na criação da conta. Ajustes negativos e transferências
ficam para etapas seguintes; a API os rejeita por enquanto para evitar efeitos ambíguos.

A chave de idempotência é obrigatória na criação de transações. Repetir a chave e
payload retorna a transação existente; alterar o payload retorna 409. A resposta
repetida reflete o estado atual da transação. Escritas concorrentes do mesmo usuário
são serializadas por advisory lock PostgreSQL. Duplicidade com score >= 0,90 retorna
409; `force=true` permite um segundo gasto real após revisar o aviso.
PATCH e cancelamento não usam chave de idempotência nesta sprint.

## Desenvolvimento e validação

```bash
python -m venv .venv
. .venv/bin/activate
pip install -e '.[dev]'
ruff check .
ruff format --check .
mypy app migrations tests
# Use um PostgreSQL exclusivo e descartável para testes:
export DATABASE_URL=postgresql+psycopg://pet:local_dev_only@localhost:5432/pet_test
export API_TOKEN=test-token
pytest -q
```

Os testes APAGAM tabelas da base informada, validam saldo, Decimal, auditoria,
cancelamento, duplicidade, idempotência concorrente e upgrade/downgrade Alembic.
O GitHub Actions executa a mesma suíte em PostgreSQL 16.

`schema.sql` é a especificação original; Alembic 0001 a aplica integralmente e
0002 acrescenta idempotência e unicidade das categorias raiz. As tabelas futuras
existem no schema, mas não possuem endpoints ou motores implementados.

## Próximas etapas

Recorrências e parcelas (PF-301/PF-303), transferências, tags, dashboard,
importação conciliada CloFin, MCP e backups restauráveis. Nenhum dado real ou
segredo deve entrar no repositório público. A planilha enviada não foi importada.
