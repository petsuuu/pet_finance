# Pet Finance — Backlog para Codex

## Epic 0 — Fundação

### PF-001 — Criar repositório
**Objetivo:** iniciar projeto Python.
**Entregáveis:**
- FastAPI
- SQLAlchemy 2
- Alembic
- Pydantic
- Pytest
- Ruff
- mypy
- `.env.example`
- Docker Compose com PostgreSQL

**Aceite:**
- `docker compose up` sobe API + banco.
- `/health` retorna 200.
- testes rodam localmente.

### PF-002 — Configurar banco
**Depende:** PF-001
- aplicar `schema.sql` via Alembic;
- seed de usuário local;
- seed de `Carteira`.

**Aceite:** migrations sobem e descem sem erro.

---

## Epic 1 — Núcleo financeiro

### PF-101 — Accounts
CRUD básico e cálculo de saldo.

### PF-102 — Categories
Hierarquia pai/filho + classe essencial/fundamental/supérflua.

### PF-103 — Transactions
Criar, listar e atualizar transações.

**Regras:**
- `amount > 0`;
- status padrão `PENDING`;
- escrita gera audit log;
- decimal exato.

### PF-104 — Transfers
Transferência entre duas contas em uma única transação lógica.

### PF-105 — Cancellation
Cancelar sem apagar fisicamente.

---

## Epic 2 — Segurança contra erros

### PF-201 — Idempotência
Adicionar `idempotency_key`.

**Aceite:** mesma chave chamada duas vezes não duplica transação.

### PF-202 — Duplicate detection
Implementar score de duplicidade.

### PF-203 — Audit log
Registrar create/update/cancel.

---

## Epic 3 — Recorrências e parcelas

### PF-301 — Recurring transactions
CRUD + cálculo de próxima ocorrência.

### PF-302 — Daily recurrence audit
Identificar:
- PENDING
- OVERDUE
- MATCHED
- AMOUNT_MISMATCH

### PF-303 — Installment plans
Criar plano de parcelamento.

### PF-304 — Installment generator
Gerar parcelas futuras com numeração `n/total`.

### PF-305 — Installment audit
Detectar parcela faltante/inconsistente.

---

## Epic 4 — Tags, orçamento e metas

### PF-401 — Tags
Many-to-many em transações.

### PF-402 — Monthly budgets
Orçamento por categoria/mês.

### PF-403 — Financial goals
CRUD de metas.

---

## Epic 5 — Dashboard

### PF-501 — Monthly summary
Receitas, despesas, saldo e classes de gasto.

### PF-502 — Commitments
Somar recorrências e parcelas pendentes.

### PF-503 — Free money
`saldo - compromissos - margem de segurança`.

### PF-504 — Month forecast
Projeção de fechamento.

### PF-505 — Top categories
Top gastos do mês.

---

## Epic 6 — Importação CloFin

### PF-601 — Import preview
Ler exportação sem persistir.

### PF-602 — Mapping
Mapear:
- contas;
- categorias;
- status;
- tipos;
- external IDs.

### PF-603 — Idempotent import
Reimportação não cria duplicados.

### PF-604 — Reconciliation report
Comparar:
- contagem;
- totais;
- divergências;
- não conciliados.

---

## Epic 7 — MCP

### PF-701 — MCP server
Criar servidor MCP separado da API.

### PF-702 — Read tools
- list_transactions
- get_transaction
- list_accounts
- list_categories
- monthly_summary
- financial_dashboard

### PF-703 — Write tools
- create_transaction
- update_transaction
- cancel_transaction

### PF-704 — Recurrence/installment tools
- list_recurrences
- create_recurrence
- list_installments
- create_installment_plan

### PF-705 — Audit tools
- audit_transactions
- audit_recurring_payments
- audit_installments
- find_duplicate_transactions

---

## Epic 8 — Backup

### PF-801 — JSON backup
Backup diário.

### PF-802 — XLSX export
Exportação mensal legível.

### PF-803 — Restore test
Teste automatizado de restauração.

---

## Ordem recomendada

Sprint 1:
PF-001, PF-002, PF-101, PF-102, PF-103, PF-203

Sprint 2:
PF-201, PF-202, PF-301, PF-303

Sprint 3:
PF-302, PF-304, PF-305, PF-401

Sprint 4:
PF-501, PF-502, PF-503, PF-504, PF-505

Sprint 5:
PF-601, PF-602, PF-603, PF-604

Sprint 6:
PF-701 a PF-705

Sprint 7:
PF-801 a PF-803 + hardening

## Definition of Done

Toda tarefa concluída deve ter:
- teste automatizado;
- migration quando necessário;
- validação de entrada;
- tratamento de erro;
- documentação curta;
- nenhuma chave/segredo versionado;
- compatibilidade com PostgreSQL.