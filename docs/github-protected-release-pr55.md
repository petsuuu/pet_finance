# PR #55 — Alternativa segura para administrador único

**Decisão:** o projeto tem somente um administrador autorizado. Portanto,
não alegar aprovação independente, separação de funções ou revisão por
terceiro. A PR #54 com Ed25519 permanece independente e sem alterações.

## Controle disponível sem Mac ou serviços adicionais

- O workflow `Solo manual release readiness (no deploy)` aceita apenas
  disparo manual `workflow_dispatch`, na `main` e pelo ator `petsuuu`.
- O proprietário digita o SHA completo de 40 caracteres e a confirmação
  exata `VERIFY READINESS <sha>`. O script confere esse SHA com
  `GITHUB_SHA` e com o `git rev-parse HEAD` do checkout.
- Os inputs e a identidade do executor fazem parte do histórico da
  execução do GitHub Actions. O resultado `PASS` confirma apenas
  **intenção explícita de executar a verificação de prontidão**.
- O workflow usa somente `contents: read`, sem ambiente `production`,
  sem tokens Render/MCP, sem transferência financeira e sem capacidade
  de fazer merge ou deploy.
- Qualquer branch diferente de `main`, SHA divergente, ator inesperado,
  confirmação incorreta ou evento não manual resulta em `BLOCKED`.
- Para proteger contra ações não autorizadas por pessoas com acesso
  à conta, proteger MFA/passkeys e limitar quem pode disparar workflows.

## Limites de segurança

Isto **NÃO** é aprovação independente, assinatura Ed25519, proteção contra
comprometimento da conta GitHub, nem substitui aprovação humana
separada e explícita antes de um deploy. Um mesmo administrador com
permissões completas pode alterar código e configurações de segurança;
a auditoria do GitHub registra os eventos, mas não cria separação
criptográfica de papéis. Os inputs são conferências de intenção,
não segredos nem fatores adicionais de autenticação.

Não depender de `required_reviewers` ou `prevent_self_review` para
o cenário solo: a configuração inicialmente proposta na PR #55
exigia um segundo revisor e impediria a sua utilização legítima.

## Ainda necessários antes de publicação

1. CI completo aprovado na `main` e commit exato selecionado.
2. Comprovação do teste HTTP/MCP autenticado de leitura real, com
   credenciais temporárias, escopo mínimo e sem logs sensíveis.
3. Auto-Deploy OFF verificado e baseline financeiro novo imediatamente
   antes da publicação, com qualidade OK.
4. Autorização específica e explícita do usuário para **cada** merge
   e **cada** deploy manual, independentemente de um `PASS` de readiness.
5. Pós-deploy: comparação integral de contas, saldos, transações ativas,
   arquivadas e auditoria, com resultado `MATCH`; qualquer divergência
   exige parar e investigar sem alterar dados.

O fluxo de readiness somente estará visível para disparo manual na
`main` depois de merge autorizado do arquivo de workflow. Este projeto
não possui atualmente um executor conectado seguro para rodar o teste
HTTP autenticado com segredos. Sem esse teste, o parecer operacional
permanece **NO-GO para publicação**.

**Status:** a PR #55 pode ser homologada tecnicamente e permanece
Draft até a revisão; nenhum merge ou deploy foi executado.
