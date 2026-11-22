# PR #54 — Orquestração segura de auditoria de deploy manual

## Limites de segurança

`app.services.deploy_orchestrator` encadeia **somente operações de leitura**.
Não há método de merge, deploy, transferências ou atualização de configuração.
A biblioteca não pode aprovar nem iniciar deploys: `PASS` é apenas o resultado
da auditoria de evidências. Cada publicação exige autorização humana separada.
Auto-Deploy permanece OFF; nunca é modificado pelo orquestrador.

## Adaptador de produção

O operador fornece uma implementação confiável de `ReadOnlySources`:
- `render_service()` — GET do serviço, com `autoDeploy=no` e
  `autoDeployTrigger=off`;
- `main_commit()` — SHA exato da branch `main` no GitHub;
- `plugin_info()` — ferramenta MCP via OAuth;
- `financial_snapshot()` — ferramenta MCP `financial_audit_snapshot`;
- `latest_deployment()` — deploy efetivamente LIVE no Render;
- `compare_financial_audit(baseline)` — comparação MCP somente leitura.

Não forneça ao adaptador nenhuma capacidade de escrita. Credenciais e tokens
não devem ser registrados nos logs, na PR ou no repositório. O operador precisa
validar externamente a identidade das fontes e a autorização para a publicação.
Esta PR **não instala nem configura um adaptador de rede** e não faz sondagens
automáticas sobre produção.

## Pré-deploy (sem publicar)

Obtenha o commit exato autorizado e uma chave Fernet de 32 bytes devidamente
gerada, armazenada em gerenciador de segredos externo ao repositório.
Use um diretório local privado, fora do GitHub, acessível só ao operador.

```python
from pathlib import Path
from app.services.deploy_orchestrator import prepare_predeploy

result = prepare_predeploy(
    trusted_read_only_source,
    service_id="srv-confirmado-no-render",
    commit="sha40-autorizado-no-github",
    plugin_version="versao-esperada",
    vault_dir=Path("/local/privado/audit-baselines"),
    encryption_key=secret_fernet_key,
)
```

A rotina verifica evidências, obtém o snapshot completo e grava um arquivo
`baseline-<commit>.sealed` criptografado/autenticado. Arquivo criado com
permissão 0600 e diretório 0700, sem sobrescrita. Não imprime saldos e não
armazena a chave. Uma falha retorna `BLOCKED`, sem liberar publicação.
A repetição para o mesmo commit **não sobrescreve** o baseline existente;
capture outro em um diretório privado novo. Não use NFS, diretórios compartilhados
ou links simbólicos como cofre confiável. Guarde backup criptografado e chave
separadamente segundo a política de retenção da organização.

A origem do baseline deve ser atestada pelo operador: criptografia atesta
integridade do arquivo com a chave, **não comprova** que o snapshot veio do
Render correto. A autorização humana de deploy é um processo externo.

## Pós-deploy (sem corrigir dados)

Somente **depois** de outro processo publicar o commit expressamente
autorizado, invoque:

```python
from pathlib import Path
from app.services.deploy_orchestrator import verify_postdeploy

result = verify_postdeploy(
    trusted_read_only_source,
    evidence_file=Path("/local/privado/audit-baselines/baseline-<commit>.sealed"),
    encryption_key=secret_fernet_key,
    service_id="srv-confirmado-no-render",
    commit="sha40-autorizado-no-github",
    plugin_version="versao-esperada",
)
```

Valide `status=PASS`, commit LIVE, Auto-Deploy OFF, versão MCP e comparação
com `MATCH` em todas as cinco seções (contas, saldos, ativos, arquivados,
histórico). Em qualquer ausência, corrupção, falha OAuth, divergência de bucket,
qualidade ou commit, registre `BLOCKED` e **interrompa**. Movimentações legítimas
concorrentes também podem causar divergência: exija revisão humana e nunca
corrija lançamentos, faça rollback ou redeploy automaticamente.

## Testes

`tests/test_deploy_orchestrator.py` usa fontes simuladas e `tmp_path`,
sem credenciais, Render real ou banco financeiro. Execute o CI existente com
Ruff, MyPy e Pytest em ambiente de testes descartável. A criptografia usa
`cryptography.Fernet`, declarada como dependência direta.

**Não execute testes destrutivos contra DATABASE_URL de produção.**
