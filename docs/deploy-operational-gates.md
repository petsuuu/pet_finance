# PR #54 — Checklist operacional para homologação sem Mac

Status: **BLOCKED para merge/deploy** até cumprir integralmente os gates abaixo.
Os testes automatizados Ed25519 usam chaves efêmeras **apenas para teste**; não são
uma autoridade de assinatura confiável para produção.

## Gate 1 — Aceitação HTTP autenticada do adaptador da PR

**Executor:** operador autorizado em ambiente de execução isolado, não em workflow
automático de pull request. Essa etapa consulta produção em modo leitura e não deve
executar a suíte de testes contra o banco de produção.

1. Obter, por canal autenticado, a URL HTTPS do MCP com path exato `/mcp` e a
   origem HTTPS confiável. Não inferir a origem a partir de uma URL não validada.
2. Provisionar credenciais temporárias, com escopo mínimo disponível, para
   Render (leitura do serviço), MCP OAuth e GitHub (leitura do repositório).
   Manter as credenciais somente no gerenciador de segredos do executor. Nunca
   adicioná-las ao código, à PR, aos logs, a artefatos ou ao chat.
3. No ambiente isolado e aprovado para consulta, configurar:
   - `PF_ACCEPT_REPO=petsuuu/pet_finance`
   - `PF_ACCEPT_SERVICE_ID=srv-db1c4cugekts73d4uom0`
   - `PF_ACCEPT_MCP_URL` e `PF_ACCEPT_TRUSTED_MCP_ORIGIN` verificados
   - `PF_ACCEPT_RENDER_TOKEN`, `PF_ACCEPT_MCP_TOKEN` (secretos)
   - `PF_ACCEPT_GITHUB_TOKEN` (opcional para repositório público)
4. Executar `python -m app.services.deploy_acceptance` usando **o código
   revisado da PR**, sem registrar tráfego HTTP bruto, environment ou traceback.
   Registrar somente commit, horário UTC, resultado `PASS`/`BLOCKED`,
   identificação do executor e indicadores não financeiros.
5. Exigir: origem confiável, handshake MCP correto, `structuredContent`,
   plugin 0.4.0 somente leitura, Auto-Deploy OFF, `main == LIVE`,
   snapshot com qualidade OK e comparação `MATCH` de todas as seções.
   Ausência de configuração, token, resposta incompatível ou divergência
   significa **BLOCKED**.
6. Revogar/expirar as credenciais temporárias após o teste.

**Limite:** um `PASS` aqui confirma apenas o caminho HTTP e os contratos de
leitura. Não é autorização para merge ou deploy. A implementação atual faz
POST JSON-RPC ao MCP, mas invoca somente as três ferramentas permitidas.

## Gate 2 — Autoridade independente Ed25519 para produção

**Não usar** chaves geradas pelo CI, pelo próprio verificador, pelo Render,
nem chaves privadas commitadas ou compartilhadas com o pipeline de deploy.

Requisitos mínimos:
- Operador/aprovador humano autenticado e distinto do contexto que executa
  ou verifica o deploy, com confirmação específica do SHA, serviço e execução.
- Chave privada provisionada em cofre de assinatura dedicado, com acesso
  restrito, trilha auditável, rotação e procedimento de revogação.
- Distribuição autenticada dos 32 bytes da chave pública ao verificador.
- Claims canônicas vinculadas a `authorization_id`, `execution_id`,
  `service_id`, SHA40, versão `0.4.0`, horário, expiração e `approved=true`.
- Comprovação de testes negativos: assinatura inválida, expiração, troca de
  commit/serviço/execução, chave revogada e ausência de aprovação bloqueiam.
- O mecanismo atual não consulta revogação on-line. Em caso de comprometimento,
  retirar a chave pública confiável do verificador antes de qualquer operação.

Uma alternativa sem Mac é uma autoridade de assinatura **externa ao processo
de deploy** em ambiente gerenciado e protegido, provisionada por responsável
autorizado. Só GitHub Actions + chave efêmera de CI não satisfaz este gate.

## Gate 3 — Decisão antes de publicar

- Confirmar novamente commit de `main`, commit LIVE, CI da branch e Auto-Deploy OFF.
- Capturar baseline **novo** imediatamente antes da operação e confirmar qualidade.
- Registrar evidências dos gates 1 e 2; exigir autorização humana específica de
  merge e, separadamente, de deploy.
- Se qualquer gate faltar, manter a PR em Draft, não realizar merge/deploy
  e não alterar dados financeiros.
- Após um deploy autorizado, comparar integralmente contas, saldos, ativos,
  arquivados e histórico, com resposta `MATCH`; divergências exigem revisão
  humana antes de quaisquer ações.

## Evidências existentes em 22/11/2026

- CI do commit `d313aec7e6acfa4c3d64f5fe56463176b31cf3e6`:
  GitHub Actions #45068772408: **SUCCESS**.
- Verificação do Render: `autoDeploy=no`, `autoDeployTrigger=off`,
  branch `main`.
- Consultas separadas aos conectores GitHub, Render e Pet Finance:
  base/LIVE coincidentes e auditoria MCP 0.4.0 `MATCH`, qualidade OK.
- **Não há evidência de execução autenticada do ReadOnlyHttpAdapter em produção
  nem de autoridade Ed25519 de produção provisionada.**

Nenhum resultado acima autoriza merge ou publicação.
