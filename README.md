# Pet Finance

## Cancelamento com prévia e estabelecimentos

`POST /api/v1/recurrences/{id}/cancel` recebe `effective_date` e `preview` (padrão
`true`). A prévia lista as previsões pendentes afetadas sem gravar. Com `preview=false`,
a regra termina antes da data indicada e os lançamentos PENDING vinculados desde essa
data ficam CANCELLED. Pagamentos anteriores, inclusive antecipados, atrasos antes da
data e parcelamentos são preservados. IDs e auditoria permanecem disponíveis. Não há
exclusão física e repetir o cancelamento não cria novos lançamentos.

`POST /api/v1/merchants/setup` cadastra nome conhecido, `aliases` confirmados do
cartão/Pix e `default_category_id`. `GET /api/v1/merchants/resolve?raw_name=...`
normaliza caixa, acentos e separadores e procura apenas aliases cadastrados. Não há
classificação por aproximação ou busca externa automática. Um alias não pode pertencer
a dois estabelecimentos do mesmo usuário. `PATCH /api/v1/merchants/{id}` altera a
categoria habitual, sem alterar lançamentos anteriores.

Novos lançamentos podem informar `merchant_id`. Para despesas sem categoria explícita,
usa-se a categoria habitual ativa; uma categoria explícita sempre prevalece, pois o
produto comprado pode diferir da atividade habitual. Intermediadoras genéricas devem
ser esclarecidas antes de cadastrar aliases. As ferramentas MCP expõem a prévia de
cancelamento, consulta, resolução, cadastro e atualização de estabelecimentos.

Execute `alembic upgrade head` antes da versão com estas ferramentas. A migração
0005 adiciona cadastro e vínculo opcional; não recategoriza dados antigos.

API financeira pessoal em Python 3.12, FastAPI, SQLAlchemy 2 e PostgreSQL.
Sprint 1: contas, categorias, transações e auditoria. O CloFin permanece a fonte
oficial até conciliação e validação da migração.

## Executar localmente

```bash
cp .env.example .env
# Edite API_TOKEN com um token longo e aleatório.
docker compose up --build
```

A API fica em http://localhost:8000/docs; `/health` consulta o banco.
Nas rotas `/api/v1`, envie `Authorization: Bearer SEU_TOKEN`.
O Compose aplica as migrações e cria um usuário local e a conta Carteira.
O volume `postgres_data` mantém os dados entre reinícios.
Credenciais do Compose são apenas para desenvolvimento local; não publique
esse ambiente na internet. A autenticação é de usuário único nesta versão.

## Primeiro lançamento

1. `GET /api/v1/accounts`: copie o `account_id` da Carteira.
2. `POST /api/v1/categories`: `{"name":"Combustível"}`; copie o `category_id`.
3. `POST /api/v1/transactions`:

```json
{
  "type": "EXPENSE",
  "status": "POSTED",
  "description": "Gasolina de teste",
  "amount": "50.00",
  "transaction_date": "2026-10-04",
  "account_id": "UUID_DA_CONTA",
  "category_id": "UUID_DA_CATEGORIA",
  "idempotency_key": "teste-gasolina-001"
}
```

4. Consulte as transações, o saldo em `/accounts` e `/audit/transactions`.
5. Cancele o teste em `POST /transactions/{id}/cancel`.

Valores são Decimal e retornam como strings. Apenas transações POSTED afetam o
saldo; cancelamento mantém o registro e reverte seu efeito. PENDING não afeta o saldo.
Não há exclusão física. Contas e categorias podem ser desativadas por PATCH.
Saldo inicial é definido apenas na criação da conta. Ajustes negativos e transferências
ficam para etapas seguintes; a API os rejeita por enquanto para evitar efeitos ambíguos.

A chave de idempotência é obrigatória na criação de transações. Repetir a chave e
payload retorna a transação existente; alterar o payload retorna 409. A resposta
repetida reflete o estado atual da transação. Escritas concorrentes do mesmo usuário
são serializadas por advisory lock PostgreSQL. Duplicidade com score >= 0,90 retorna
409; `force=true` permite um segundo gasto real após revisar o aviso.
PATCH e cancelamento não usam chave de idempotência nesta sprint.

## Desenvolvimento e validação

```bash
python -m venv .venv
. .venv/bin/activate
pip install -e '.[dev]'
ruff check .
ruff format --check .
mypy app migrations tests
# Use um PostgreSQL exclusivo e descartável para testes:
export DATABASE_URL=postgresql+psycopg://pet:local_dev_only@localhost:5432/pet_test
export API_TOKEN=test-token
pytest -q
```

Os testes APAGAM tabelas da base informada, validam saldo, Decimal, auditoria,
cancelamento, duplicidade, idempotência concorrente e upgrade/downgrade Alembic.
O GitHub Actions executa a mesma suíte em PostgreSQL 16.

`schema.sql` é a especificação original; Alembic 0001 a aplica integralmente e
0002 acrescenta idempotência e unicidade das categorias raiz. As tabelas futuras
existem no schema, mas não possuem endpoints ou motores implementados.

## Próximas etapas

Recorrências e parcelas (PF-301/PF-303), transferências, tags, dashboard,
importação conciliada CloFin, MCP e backups restauráveis. Nenhum dado real ou
segredo deve entrar no repositório público. A planilha enviada não foi importada.

## Sprint 2: planos financeiros

`POST /api/v1/recurrences` e `GET /api/v1/recurrences` criam/listam regras
mensais. Campos: description, account_id, expected_amount, due_day (1–31),
start_date; category_id, end_date, tolerance_amount e notes são opcionais.
`PATCH /api/v1/recurrences/{id}` altera valor, vencimento e atividade.
Dias inexistentes são ajustados ao último dia do mês, incluindo anos bissextos.
A próxima ocorrência é calculada a partir da data inicial e recalculada a partir
 da ocorrência existente quando o vencimento muda.

`POST /api/v1/installments` e `GET /api/v1/installments` criam/listam planos
com description, account_id, installment_amount, total_installments e
first_installment_date. O total é calculado com Decimal. Listagens aceitam limit/offset.

Ambos exigem Bearer token, validam referências e geram audit log. Criar planos
não gera transações nem altera saldo. Geração de parcelas e auditoria diária
ficam para a sprint 3. Idempotência e detecção de duplicidade de transações
já estão implementadas na sprint 1; criação de planos não é idempotente.

## Sprint 3: geração e auditoria

`POST /api/v1/installments/{id}/generate` gera parcelas PENDING, com numeração
única e datas ancoradas no dia original. Reexecução e concorrência não duplicam
parcelas, incluindo as canceladas. Não altera o saldo até o pagamento.
`GET /api/v1/audit/installments?identity=<id>` verifica datas, valores e parcelas ausentes.
`GET /api/v1/audit/recurrences?start_date=YYYY-MM-DD&end_date=YYYY-MM-DD`
consulta ocorrências mensais (janela máxima de 366 dias); as_of é opcional.
Pagamentos são conciliados por recurrence_id e transaction_date igual ao vencimento;
pagamentos antecipados devem preservar essa data financeira. Esta auditoria é
uma consulta: agendamento diário depende da hospedagem e ainda não foi instalado.
`POST /api/v1/tags` cria uma tag com name; `GET /api/v1/tags` lista tags.
Transações aceitam tags como lista de UUIDs e recurrence_id opcional.
`GET /api/v1/transactions?tag=<uuid>` filtra por tag. Vínculos geram auditoria.

## Sprint 4: dashboard mensal

`GET /api/v1/dashboard/monthly?year=2026&month=10&as_of=2026-10-04&safety_margin=100`
retorna saldo na data, receitas e despesas realizadas, resultado mensal,
compromissos pendentes, receitas previstas, dinheiro livre, projeção e top 5 categorias.
Valores monetários usam Decimal e são retornados como strings. Meses anteriores
usam o último dia como as_of padrão; o mês atual usa hoje em São Paulo.

Saldo inclui saldo inicial e lançamentos POSTED até as_of. Resultado mensal
exclui saldo inicial, transferências, ajustes e pagamento de cartão; estornos
reduzem despesas. Compromissos incluem despesas PENDING até o fim do mês,
inclusive atrasadas, e regras mensais sem ocorrência vinculada no mês.
Parcelas geradas já são transações e não são contadas novamente. Regras com
ocorrência vinculada, inclusive cancelada, não adicionam previsão virtual.

Dinheiro livre = saldo − compromissos − margem. Projeção de saldo no fim do mês
= saldo + receitas previstas − compromissos. A margem é apresentada separadamente
na projeção após margem; não é tratada como gasto. Não há estimativa de gastos
variáveis futuros. Poupança efetiva retorna null: não pode ser deduzida do resultado.
Planos de parcelas ainda não gerados não entram nos compromissos. Categorias
sem classe são exibidas como não classificadas. Nenhum dado real é importado aqui.

## Sprint 5: importação CloFin

`POST /api/v1/imports/clofin/preview` valida um snapshot JSON com accounts,
categories e transactions das listagens CloFin, sem gravar.
`POST /api/v1/imports/clofin/commit` importa atomicamente e registra um lote.
`GET /api/v1/imports/{batch_id}/reconciliation` compara valores, tipos, status,
datas, contas e categorias. Reimportar IDs iguais não duplica; alterações da
origem geram conflito e exigem conciliação explícita. Não remove dados existentes.

O importador suporta BRL, receitas/despesas/estornos/rendimentos e ajustes
positivos. Transferências, cartões, ajustes negativos ou referências ausentes
são bloqueados. Contas são mapeadas por nome e saldo inicial; categorias por
nome e hierarquia. Recorrências/parcelamentos descritos em notas são preservados
como transações, sem inferir regras ou gerar novas parcelas. Notas preservam
as marcações originais; extração de tags a partir de texto exige revisão.

Use somente banco privado. O snapshot real não deve entrar no GitHub público.
A conciliação por lote não prova saldo global nem migração completa; valide
saldo de cada conta e referências antes de mudar a fonte oficial.


## ChatGPT MCP connection (OAuth)

MCP is disabled until both `MCP_PUBLIC_URL` and `MCP_LOGIN_PASSWORD` are configured.
The existing REST API keeps using `API_TOKEN`. The MCP connection does not accept
that token and never exposes it to ChatGPT.

1. Deploy this version; Alembic creates `oauth_records` without changing financial rows.
2. In Render → Environment, set `MCP_PUBLIC_URL` to your HTTPS origin (no trailing
   path), e.g. `https://your-service.onrender.com`. Set `MCP_LOGIN_PASSWORD` to a
   separate randomly generated secret of at least 24 characters. Save and redeploy.
3. In ChatGPT → Plugins → Add → Create custom MCP server, use the name **Pet Finance**,
   URL `https://your-service.onrender.com/mcp`, and **OAuth**.
4. Use dynamic client registration (DCR) if the advanced settings offer a choice.
   No client secret or client ID is entered manually. The server advertises DCR.
5. Complete the OAuth login on the Pet Finance page using `MCP_LOGIN_PASSWORD`,
   then authorize the finance scope (read, create, update). Never paste this password
   or `API_TOKEN` into a conversation or the public repository.
6. Test **list_accounts** and the monthly dashboard first. Verify the returned data
   before requesting real writes. Tools are available only after authentication.

Tools: `list_accounts`, `list_categories`, `list_tags`, `list_transactions`,
`monthly_dashboard`, `list_recurring_and_installments`, `create_transaction`,
`update_transaction`. No delete or duplicate-check bypass is exposed. Existing
CloFin installments are transactions; migration does not automatically create
recurrence rules or installment plans.

The OAuth server is for the configured single owner (`USER_ID`), not a multiuser
product. It supports ChatGPT HTTPS callbacks on `chatgpt.com` (the stable callback
or `/connector/oauth/{callback_id}`); other clients require an explicit code change
and review of their exact callback. Authorization uses S256 PKCE, browser-bound
consent with CSRF and Origin checks, two-minute single-use codes, one-hour access
tokens, and rotating refresh tokens valid for 30 days. Tokens are stored only as
SHA-256 hashes in PostgreSQL. The login form and token responses use no-store.

Client registration, authorization starts and login attempts are limited globally
for this owner over five-minute windows. Login attempts are limited to 20; a flood
can temporarily prevent login until the window expires. Tokens and registered
clients survive restart. Changing `MCP_LOGIN_PASSWORD` invalidates all outstanding
clients, codes and tokens; reconnect ChatGPT afterward. Clear both MCP variables
to disable the integration without modifying financial data.

Future full snapshot reconciliation still compares against the original imported
values: intentional edits such as a payment update can report differences from
that historical snapshot. Do not reimport it to undo those edits.

### Recorrências pelo MCP

`setup_recurrence` cria uma regra mensal ou anual (`frequency=YEARLY`, `month_of_year` obrigatório) e vincula explicitamente os IDs de lançamentos já
existentes (um por mês). Consulte conta, categoria, valores e datas antes de configurar;
use `end_date` para respeitar o período de continuidade confirmado. Repetir a mesma
configuração com os mesmos IDs reutiliza a regra. Os pagamentos existentes mantêm seus
valores, status e datas reais, inclusive pagamentos antecipados dentro do mês.

`generate_recurrence` cria somente ocorrências pendentes que faltam no período solicitado
(até 366 dias), respeitando início/fim da regra. Meses vinculados, inclusive cancelados,
não são gerados novamente. A operação não cria um cron: execute-a pelo conector ou por
um agendamento autenticado. Após atualizar o servidor, atualize as ferramentas do plugin
Pet Finance para carregar essas duas operações.


`update_recurrence` edita a regra existente por ID: use `end_date` para estender o
horizonte confirmado ou `active=false` para interromper novas gerações. Mantém
vínculos, datas e valores dos lançamentos existentes; previsões já registradas não
são canceladas automaticamente. A edição é serializada com a geração. Regras anuais
só geram e entram na projeção no mês de renovação. Atualize as ferramentas do plugin
após publicar para carregar a edição e o novo esquema de configuração.
## Agenda mensal resumida

`GET /api/v1/dashboard/agenda?year=2026&month=10&as_of=2026-10-05` reúne despesas
avulsas, recorrências e parcelas numa lista por data, com totais de pagos, pendentes e
atrasados, contas do dia e próximos sete dias. Pendências registradas de meses anteriores
continuam visíveis. A ferramenta MCP `monthly_agenda` disponibiliza a mesma leitura.

`recorded=false` e `missing_forecasts` identificam ocorrências esperadas que ainda não
têm lançamento; a consulta não grava nada. Ocorrências vinculadas, inclusive pagas ou
canceladas, impedem outra previsão na agenda. Estimativas ausentes são buscadas apenas
no mês solicitado. Datas dos registros são as datas dos lançamentos, não um vencimento
ou data de pagamento reconstruídos. Transferências, pagamentos de cartão e ajustes
técnicos de saldo não entram nos totais de consumo.
