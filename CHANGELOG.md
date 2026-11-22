# Histórico de versões do Pet Finance (conector MCP)

O pacote de aplicação `pet-finance` define a versão oficial do conector MCP em
`pyproject.toml`. O servidor publica a mesma versão no handshake MCP e na
ferramenta autenticada e somente leitura `plugin_info`.

## [0.2.0] - Em revisão (PR #49, 22/11/2026)

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
- Esta entrada documenta um **candidato a lançamento**, não comprova deploy
  ou criação de tag Git.
- Merge, publicação manual no Render e eventual tag `v0.2.0` dependem das
  respectivas autorizações e da auditoria financeira.

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
