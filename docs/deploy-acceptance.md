# PR #54 — Aceitação somente leitura e autoridade de aprovação

## Teste de aceitação isolado

O módulo `app.services.deploy_acceptance` é executado **somente por
solicitação do operador**. Ele não é job automático do GitHub Actions e não
executa merge, deploy, transferências ou alteração de configuração.

Para executar em estação segura, obtenha tokens de privilégios mínimos no
gerenciador de segredos (sem registrar os valores neste repositório), e
disponibilize os nomes de configuração abaixo somente no processo da execução:

| Variável | Origem e restrição |
| --- | --- |
| `PF_ACCEPT_REPO` | Repositório autorizado, por exemplo `petsuuu/pet_finance` |
| `PF_ACCEPT_SERVICE_ID` | Serviço Render `srv-...` autorizado |
| `PF_ACCEPT_MCP_URL` | URL HTTPS real do MCP terminado em `/mcp` |
| `PF_ACCEPT_TRUSTED_MCP_ORIGIN` | Origem HTTPS do MCP confiável, fixada independentemente da URL |
| `PF_ACCEPT_RENDER_TOKEN` | Credencial apenas com permissão de leitura do serviço |
| `PF_ACCEPT_MCP_TOKEN` | Token OAuth com a menor permissão efetivamente disponibilizada |
| `PF_ACCEPT_GITHUB_TOKEN` | Token GitHub somente leitura, opcional para repositório público |

Execute `python -m app.services.deploy_acceptance`. Retorna apenas
`PASS` ou `BLOCKED` e indicadores sem saldos, hashes financeiros ou tokens;
código de saída 0 significa que o contrato de leitura respondeu corretamente.
O teste consulta GitHub e Render via GET e apenas as três ferramentas MCP
permitidas. Ele captura e compara um snapshot **em memória**, sem gravar
arquivo de baseline nem alterar lançamentos. Qualquer divergência bloqueia.

**Atenção:** `PASS` neste teste comprova apenas conectividade e esquema,
não concede autorização de publicação nem substitui a auditoria
pré/pós-deploy. Os scopes OAuth do serviço podem permitir operações de
escrita mesmo quando este cliente só invoca ferramentas de leitura.
Use credenciais temporárias, acesso e saída sem logs de request/response.

## Autoridade de assinatura Ed25519

O orquestrador usa `SignedApprovalVerifier` e precisa de um comprovante
assinado por uma autoridade **independente**. A PR contém somente o
verificador; ela não provisiona uma autoridade, nem deve criar uma
chave privada em produção.

Responsabilidades da autoridade externa:
1. Autenticar o aprovador humano e registrar a decisão com evidência
   auditável, incluindo identidade, horário e consentimento específico.
2. Exigir e assinar as claims canônicas exatas (formato
   `pet-finance-deploy-approval-v1`): `authorization_id`,
   `execution_id`, `service_id`, SHA de 40 caracteres, versão MCP,
   `issued_at`, `expires_at` e `approved=true`.
3. Guardar a chave privada Ed25519 em HSM/KMS ou cofre dedicado e isolado;
   fornecer somente os **32 bytes da chave pública** ao operador por canal
   autenticado; nunca compartilhar chave privada com Render ou CI.
4. Manter log append-only e revogação/rotação de chaves. A implementação
   atual não consulta lista de revogação: revogar exige invalidar a
   chave pública confiável no ambiente operador ou adicionar verificação
   online autenticada antes de utilizar em produção.
5. Limitar o TTL do recibo a até duas horas. Se expirado, nova aprovação
   explícita é necessária, e não deve ser reemitida automaticamente.

Formato de recibo ilustrativo (**assinatura não válida**):

```json
{
  "claims": {
    "format": "pet-finance-deploy-approval-v1",
    "authorization_id": "approval-example-0001",
    "execution_id": "execution-example-0001",
    "service_id": "srv-example",
    "commit": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
    "plugin_version": "0.4.0",
    "issued_at": "2026-11-22T19:00:00+00:00",
    "expires_at": "2026-11-22T19:30:00+00:00",
    "approved": true
  },
  "signature": "BASE64_ASSINATURA_ED25519_EXTERNA"
}
```

**Bloqueadores operacionais:** falta autoridade de assinatura
provisionada, distribuição de chave pública de confiança, ambiente seguro
do cofre, credenciais de leitura de privilégio mínimo e teste HTTP
autenticado do adaptador diretamente contra produção.
Nada disso foi criado ou configurado por esta PR. O Auto-Deploy continua
OFF e merge/deploy requerem autorização separada.
