# Autoridade local Ed25519 — macOS Keychain

A PR #54 oferece um fluxo **local e sem serviços adicionais**. O Mac gera
uma chave Ed25519 e a guarda como item de senha genérica no Keychain.
A chave privada **não é impressa nem enviada** ao GitHub, Render ou ChatGPT.
O processo local somente assina comprovantes de autorização; **nunca faz
merge, deploy ou alteração financeira**.

## Pré-requisitos e limites

- macOS com login no Keychain do usuário e Python do projeto com
  `cryptography` instalado
- Executar os comandos no Terminal do próprio Mac, com acesso local interativo
- A segurança depende da sessão do macOS, ACLs do Keychain, acesso físico,
  proteção de disco e do usuário do sistema
- Uma senha genérica no Keychain **não é chave não exportável no Secure Enclave**.
  Processos autorizados a ler esse item conseguem obter a seed. Essa solução
  é apropriada como proteção local, não equivalente a um HSM
- Não execute no CI, Render, contêiner público nem em ambiente compartilhado
- O utilitário não comprova identidade independente de um segundo aprovador:
  a autorização humana é a confirmação interativa no Mac. Para
  separação de funções, é necessário um operador distinto

## Inicialização (uma vez)

No checkout local da branch PR #54:

```sh
python -m app.services.deploy_local_signer init
```

O comando pede acesso ao Keychain e imprime **somente a chave pública**
em Base64, no formato `PUBLIC_KEY_BASE64=...`. Copie esse valor para
um canal local confiável; confira a origem antes de configurá-lo como
chave pública aprovada do verificador. Não altere a chave existente
repetindo o comando: o utilitário recusa sobrescrever o item do Keychain.

Para consultar novamente somente a chave pública:

```sh
python -m app.services.deploy_local_signer public
```

A chave pública tem 32 bytes após decodificação Base64. O orquestrador
pode criar um `SignedApprovalVerifier` a partir dela:

```python
import base64
from app.services.deploy_approval import SignedApprovalVerifier

verifier = SignedApprovalVerifier(base64.b64decode(public_key_base64))
```

Armazene o valor público em uma configuração restrita e verificada.
A chave privada nunca deve ser copiada para o repositório, logs ou Render.

## Assinatura de uma autorização específica

Somente **depois de uma decisão humana explícita**, execute localmente,
substituindo o commit por um SHA real e o serviço pelo identificador conferido:

```sh
python -m app.services.deploy_local_signer sign \
  --service-id srv-db1c4cugekts73d4uom0 \
  --commit SHA40_VERIFICADO \
  --authorization-id approval-20261122-0001 \
  --execution-id execution-20261122-0001 \
  --plugin-version 0.4.0 \
  --output "$HOME/approval-pr54.json"
```

O programa mostra serviço, commit e execução e exige que o usuário digite
`APROVAR` em terminal interativo. Rodar em lote ou sem TTY falha.
O comprovante é salvo com permissões 0600 e criação exclusiva, sem
sobrescrever um arquivo existente. Validade padrão: **30 minutos**,
máxima: 120 minutos. Após expirar, é necessária nova autorização humana.

Antes de autorizar qualquer futura publicação, confira novamente
a branch `main`, o commit escolhido, o Auto-Deploy OFF e a integridade
dos lançamentos. Um comprovante assinado **não executa o deploy** nem
substitui a autorização explícita para merge/publicação.

## Segurança operacional

- Ative FileVault e bloqueio de sessão do Mac
- Inspecione as permissões de acesso ao item `pet-finance-deploy-approval-v1`
  pelo Acesso às Chaves; não permita acesso indiscriminado
- Use a conta macOS do operador, evite programas desconhecidos com acesso
  ao Keychain e proteja backups locais
- Perder o Keychain ou restaurar outra identidade requer atualizar
  explicitamente a chave pública confiável
- Para revogar a chave, interrompa os processos que aceitam a antiga chave
  pública. A implementação atual não possui lista automática de revogação
- Nunca envie a seed ou exportações privadas pelo chat
- O teste HTTP direto permanece **opcional e separado**; exige tokens
  autenticados, não fornecidos ao utilitário local

## Status de implantação

Este documento e o utilitário são código na branch da PR #54.
A **chave real só será gerada quando o operador executar `init` no Mac**.
O CI usa somente uma Keychain em memória simulada; nenhum segredo real é
criado, e nenhum serviço externo adicional é provisionado.
A PR deve permanecer draft e o Auto-Deploy OFF.

## Alternativa sem uso do Mac: homologação automática no GitHub

A PR #54 inclui o job `approval-security` em `.github/workflows/checks.yml`,
executado em push/PR. Ele gera **chaves Ed25519 efêmeras apenas na memória**
e testa verificação válida, adulteração de claims, chave incorreta, expiração
e divergência de identificadores. A execução usa permissões GitHub
`contents: read`, sem secrets de produção e sem acesso ao Render.

Essa homologação não usa o Keychain, não cria chaves persistentes e **não
constitui aprovação de produção**. O processo de deploy continua bloqueado
até existir uma autoridade de produção previamente confiável e uma autorização
humana específica. O Auto-Deploy deve permanecer desligado. O fluxo local
acima fica opcional, caso futuramente seja escolhido como autoridade real.
