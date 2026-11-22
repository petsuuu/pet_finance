# PR #54 — Orquestração segura de auditoria de deploy manual

O código de orquestração **não realiza merge, deploy, transferências,
atualizações de configuração nem escrita financeira**. O resultado `PASS`
atesta somente as evidências verificadas, sem conceder autorização.
O Auto-Deploy deve continuar OFF e cada publicação exige autorização humana
obtida por um processo separado.

## Adaptador real restrito a leitura

`ReadOnlyHttpAdapter` implementa as seis funções `ReadOnlySources`:

- GitHub: GET da branch `main`, identificando o commit exato;
- Render: GET do serviço e GET do último deploy;
- Pet Finance OAuth/MCP: POST ao protocolo JSON-RPC apenas para
  `plugin_info`, `financial_audit_snapshot` e `compare_financial_audit`.
  Nenhuma ferramenta MCP de escrita é permitida.

O protocolo MCP usa POST como transporte, mas as três ferramentas
autorizadas são somente leitura. O adaptador não envia tokens de um provedor
a outro e recusa redirecionamentos HTTP. Configure o cliente com URLs HTTPS,
identidade de repositório e serviço confirmada e tokens obtidos de um gerenciador
de segredos seguro. Os testes usam `httpx.MockTransport`; **não houve
integração end-to-end autenticada real com os três provedores**. O adaptador
pressupõe transporte MCP com resposta `application/json` e
`structuredContent`; outros transportes precisam de integração validada
antes do uso operacional.

## Cofre de evidências

- Chave `Fernet.generate_key()` guardada fora do repositório, logs, variáveis
  expostas e do diretório de evidências; não perca a chave.
- Diretório local próprio do operador e privado (0700), arquivo (0600).
- Acesso via descritores de diretório, `O_NOFOLLOW` e `dir_fd`; criação
  `O_EXCL` impede sobrescrita, leitura valida dono e tipo com `fstat`.
- Após escrever, `fsync` de arquivo e diretório antes de retornar `PASS`.
  É necessário armazenamento local com semântica POSIX e durabilidade adequada;
  não use NFS, diretórios compartilhados ou contêiner efêmero como única cópia.
- Não reutilize a mesma dupla commit + execução no mesmo diretório: uma segunda
  criação bloqueia a operação para preservar a evidência original.
- Proteções não cobrem administrador/root malicioso nem provam a origem da
  informação fornecida pelo adaptador. Gerencie backup, retenção e acesso
  ao cofre separadamente.

## Vinculação da autorização e frescor

O operador informa **identificadores de referência** distintos,
`authorization_id` e `execution_id` (8–128 caracteres
alfanuméricos, hífen e sublinhado). São registrados de forma autenticada
dentro do envelope criptografado, junto a serviço, commit, versão do plugin
e snapshot integral.

Essas referências **não comprovam que a autorização ocorreu**: a aprovação
e sua trilha de auditoria devem ser validadas por um sistema independente.
A rotina bloqueia snapshots pré-deploy com mais de cinco minutos, evidências
pré-deploy usadas mais de duas horas depois e comparações pós-deploy com
mais de cinco minutos. Horários futuros e sem fuso são rejeitados.

```python
from datetime import datetime, timezone
from pathlib import Path

from app.services.deploy_orchestrator import prepare_predeploy, verify_postdeploy
from app.services.deploy_readonly_adapter import ReadOnlyHttpAdapter

source = ReadOnlyHttpAdapter(
    repo="petsuuu/pet_finance",
    service_id="srv-confirmado",
    render_token=render_token_obtido_com_segurança,
    mcp_url="https://host-confirmado.example/mcp",
    mcp_oauth_token=oauth_token_obtido_com_segurança,
    github_token=github_token_obtido_com_segurança,
)
pre = prepare_predeploy(
    source,
    service_id="srv-confirmado",
    commit="sha40-autorizado",
    plugin_version="0.4.0",
    authorization_id="approval-externo-0001",
    execution_id="execution-externa-0001",
    vault_dir=Path("/cofre-local-privado/auditorias"),
    encryption_key=chave_fernet_externa,
)
# Somente após aprovação humana e deploy manual por processo externo:
post = verify_postdeploy(
    source,
    evidence_file=Path(pre["evidence_file"]),
    encryption_key=chave_fernet_externa,
    service_id="srv-confirmado",
    commit="sha40-autorizado",
    plugin_version="0.4.0",
    authorization_id="approval-externo-0001",
    execution_id="execution-externa-0001",
)
```

`PASS` pré-deploy **não autoriza publicação**. Se um passo bloquear,
não implante, transfira dinheiro, corrija lançamentos ou faça rollback sem
nova decisão humana. `PASS` pós-deploy exige commit LIVE, Auto-Deploy OFF,
versão MCP esperada, qualidade OK e `MATCH` para todas as cinco seções,
incluindo contas inativas, lançamentos arquivados e histórico de auditoria.

## Testes e implantação

GitHub Actions executa `ruff check .`, `ruff format --check .`,
`mypy app migrations tests` e `pytest -q` em PostgreSQL descartável.
Os testes de adaptador são isolados e não usam credenciais reais.
Jamais rode a suíte destrutiva de testes contra um DATABASE_URL de produção.

A PR #54 permanece draft até revisão final e autorização explícita
para merge/deploy; não habilitar Auto-Deploy.
