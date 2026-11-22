# PR #55 — Alternativa ao controle Ed25519 da PR #54

Esta proposta usa o mecanismo nativo de **GitHub Environments** para uma
aprovação humana de execução manual. **Não é assinatura Ed25519 independente**,
e não deve ser tratada como substituição criptograficamente equivalente.

## Separação de responsabilidades

- O workflow `Manual production readiness (no deploy)` é acionado
  exclusivamente por `workflow_dispatch` e referencia `environment: production`.
- O GitHub Environment deve ter **required reviewers** e **prevent self-review**.
  A mera existência do ambiente não prova que esteja protegido.
- O verificador `github_release_gate.py` consulta a configuração do
  ambiente pelo GitHub API, e bloqueia quando a política não existe, não pode
  ser consultada, está sem aprovadores, permite autoaprovação ou a execução
  não vem de um disparo manual na `main`.
- O workflow **não publica**, não guarda credenciais de produção, não
  consulta dados financeiros e não fornece um token de deploy.
- O teste automatizado `tests/test_github_release_gate.py` cobre
  configurações válidas e negativas, mas não equivale à aprovação real.

## Passos administrativos antes de ativar

1. Confirmar se o repositório e o plano disponibilizam as regras de
   proteção de ambientes exigidas. Algumas combinações de visibilidade
   e plano não suportam required reviewers. **Sem proteção: BLOCKED**.
2. Configurar ambiente `production` com required reviewers confiáveis e
   prevent self-review; limitar branches de deployment à `main`.
   Se a conta não tiver outro aprovador autorizado, **não retirar a
   exigência** apenas para passar no gate.
3. Confirmar proteção de branch e regras de revisão no GitHub.
4. Somente após revisão/merge autorizado desta PR, uma execução manual
   na `main` pode avaliar a política e demonstrar o gate.
5. Usar separadamente o teste HTTP/MCP autenticado de privilégio mínimo;
   comparar baseline financeiro recente antes e depois de qualquer deploy
   **explicitamente autorizado**, mantendo Render Auto-Deploy OFF.

## Limitações e decisão

`PASS` significa **apenas política de ambiente observada**;
não prova que quem aprovou o workflow tinha autorização de negócio,
não atesta a integridade financeira, não substitui validação do MCP,
não registra aprovação Ed25519 e não autoriza deploy.
Um ambiente criado sem revisores não é proteção. Falhas da API e
permissões insuficientes são sempre BLOCKED.

Como não há configuração administrativa de environment disponível
nos conectores desta sessão, não foi ativada proteção real de produção
nem configurado qualquer segredo ou deploy. A PR #54 permanece
intocada, aberta e independente para comparação.

**Estado recomendado:** PR #55 em Draft; NO-GO para deploy até configurar
e auditar a proteção de ambiente e cumprir os demais gates operacionais.
