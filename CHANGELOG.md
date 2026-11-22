# Histórico de versões do Pet Finance (conector MCP)

O pacote de aplicação `pet-finance` define a versão oficial do conector MCP em
`pyproject.toml`. O servidor publica a mesma versão no handshake MCP e na
ferramenta autenticada e somente leitura `plugin_info`.

## [0.3.0] - Em revisão (PR #51, 22/11/2026)

### Adicionado
- Auditoria Inteligente v2: endpoints OAuth/Bearer somente leitura
  `GET /api/v1/audit/financial-snapshot` e
  `POST /api/v1/audit/financial-snapshot/compare`.
- Snapshot PostgreSQL `REPEATABLE READ READ ONLY` em uma única transação,
  com todas as contas (incluindo excluídas do resumo), saldos, transações
  ativas/arquivadas e histórico completo de auditoria.
- SHA-256 determinístico das linhas completas, organizado por seção, e
  verificação global de integridade, com detalhes de saldos por conta.
- Comparação dirigida por snapshot anterior mantido pelo cliente, sinalizando
  qualquer alteração, inclusive movimentações legítimas durante o deploy.
- Ferramentas MCP `financial_audit_snapshot` e `compare_financial_audit`,
  ambas de leitura e autenticadas.

### Proteções
- Snapshots não são armazenados no banco e não alteram dados financeiros.
- O snapshot anterior deve ser capturado e preservado de fonte confiável;
  hashes desacompanhados de autenticação não provam sua origem.
- Mudanças entre capturas exigem revisão; correspondência de hashes não
  comprova liquidação bancária.
- Versão `0.3.0` candidata a publicação em lote; PR #50 (`0.2.1`) já
  incorporada à `main`, mas ainda não publicada isoladamente.
- Nenhum deploy, tag ou merge é realizado por esta PR.

## [0.2.1] - Em revisão (PR #50, 22/11/2026)

### Corrigido
- Removido o parâmetro interno `rest` que vazava para seis consultas GET do MCP
  por causa de `locals()` em funções internas.
- Os parâmetros de cada ferramenta passam a ser declarados explicitamente:
  list_transactions, monthly_dashboard, monthly_agenda, daily_cashflow,
  recovery_plan e check_purchase.
- Testes verificam os parâmetros efetivamente enviados e preservam o uso
  exclusivamente de leitura e os contratos das ferramentas existentes.

### Compatibilidade e segurança
- PATCH SemVer, sem novos parâmetros públicos, mudança de SDK, migração,
  transferências ou alterações financeiras.
- A versão `0.2.1` é candidata nesta PR, não representa um deploy ou tag.

## [0.2.0] - Publicada (PR #49, 22/11/2026)

### Adicionado
- Versionamento explícito do conector MCP por SemVer.
- Ferramenta `plugin_info` para consultar nome, versão instalada, API v1 e política
  de versionamento sem acessar nem alterar dados financeiros.
- Informação de versão da aplicação no `serverInfo.version` do handshake MCP.
- Teste automático para garantir consistência entre pacote, changelog, handshake,
  catálogo de ferramentas e retorno da ferramenta; valida ausência de mutações
  financeiras.

### Compatibilidade e segurança
- Novos metadados e ferramenta aditiva; nenhuma assinatura de ferramenta existente
  ou endpoint financeiro foi alterado.
- OAuth, API v1 e dependência `mcp==1.30.0` preservados.
- Publicada no Render com pacote `pet-finance==0.2.0`; não implica
  criação de tag Git.
- Merge e deploy foram autorizados separadamente, com auditoria financeira
  pré/pós-publicação sem divergências. Tag Git não confirmada.

## [0.1.0] - Versão base anterior

Versão que o pacote declarava antes de iniciar a rastreabilidade explícita do
conector MCP. Este registro não afirma a existência de uma tag ou de uma data
de lançamento histórico.

## Política para próximas alterações

- **PATCH** (`X.Y.Z+1`): correções compatíveis, sem quebra de contratos.
- **MINOR** (`X.Y+1.0`): novas ferramentas ou campos compatíveis/aditivos.
- **MAJOR** (`X+1.0.0`): quebras em nomes, parâmetros, autenticação ou contratos,
  somente com plano de migração autorizado.
- Toda atualização do **plugin** precisa atualizar `pyproject.toml`, este
  histórico e os testes de versão/compatibilidade, na mesma PR.
- Atualizações do SDK MCP são independentes da versão do nosso plugin e exigem
  testes específicos. Nunca alterar apenas a dependência para aparentar uma
  nova versão do plugin.
- Versões planejadas em PR não significam versões publicadas. Tags e releases
  devem identificar somente commits aprovados e publicados após verificação.
