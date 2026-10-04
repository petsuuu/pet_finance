# Prompt mestre para o Codex — Pet Finance

Você está implementando o projeto **Pet Finance**, um sistema financeiro pessoal com PostgreSQL como fonte oficial.

## Objetivo imediato

Construa o MVP seguindo estes arquivos:
1. `schema.sql`
2. `api_mcp_spec.md`
3. `backlog.md`

## Stack obrigatória

- Python 3.12+
- FastAPI
- SQLAlchemy 2.x
- Alembic
- PostgreSQL
- Pydantic v2
- Pytest
- Ruff
- mypy
- Docker Compose

## Regras de engenharia

- Valores monetários: `Decimal`.
- Datas financeiras: `date`.
- Timestamps: UTC.
- Nunca excluir transações fisicamente no MVP.
- Toda alteração financeira deve gerar audit log.
- Toda criação de transação deve suportar idempotência.
- Antes de inserir uma transação, executar detecção de duplicidade.
- PostgreSQL é a fonte de verdade.
- Não implementar frontend nesta fase.
- Não acoplar MCP diretamente ao banco: MCP chama a camada de serviço/API.
- Código deve ser modular e testável.

## Estrutura desejada

```text
pet-finance/
  app/
    api/
    core/
    db/
    domain/
    models/
    schemas/
    services/
  mcp_server/
  migrations/
  tests/
  scripts/
  schema.sql
  api_mcp_spec.md
  backlog.md
  docker-compose.yml
  pyproject.toml
  .env.example
  README.md
```

## Primeira execução

Implemente apenas:
- PF-001
- PF-002
- PF-101
- PF-102
- PF-103
- PF-203

Antes de alterar código:
1. leia todos os arquivos;
2. proponha um plano curto;
3. implemente em commits lógicos;
4. rode lint, type-check e testes;
5. corrija falhas;
6. atualize README;
7. apresente resumo do que foi feito e próximos itens do backlog.

Não avance para recorrências, parcelas, MCP ou importação CloFin nesta primeira execução.