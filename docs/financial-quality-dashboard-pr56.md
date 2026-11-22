# Dashboard de Qualidade Financeira — PR #56

O novo endpoint autenticado `GET /api/v1/dashboard/financial-quality` aceita
`year`, `month`, `as_of` opcional e `limit` (1–500).

Reutiliza a auditoria de integridade já existente e o planejamento mensal
já validado. O relatório separa:

- **integrity_status:** erros contábeis, candidatas a duplicidade e
  referências/classificações a revisar. Não conclui que uma duplicata é fraude.
- **risk_status:** saldo negativo por envelope, compromissos maiores que o
  saldo sem antecipar receitas futuras, categorias acima do orçamento
  e projeção mensal insuficiente.
- **status:** CRITICAL somente quando a auditoria de integridade é crítica;
  caso contrário, REVIEW para pendências/alertas; OK quando limpo.

Valores monetários são preservados em Decimal e serializados como strings.
Os dados são apenas leitura, sem criação, pagamento, transferência, alteração
de categoria, exclusão de duplicatas ou alteração de orçamentos.

O endpoint exclui contas arquivadas/inativas e excluídas do resumo por
reutilizar o escopo operacional do dashboard. Os saldos negativos são risco
de liquidez, **não divergência contábil**. Recursos futuros ou projetados
não contam como dinheiro disponível. Limites por categoria não são saldos.

Os testes cobrem cenários de risco, caso limpo, prioridade de integridade,
autenticação, limites de parâmetros e preservação de contas, transações e
histórico de auditoria.

**Deploy:** nenhuma publicação é feita nesta PR. Um merge ou deploy futuro
exige autorização específica e baseline financeiro antes e depois.
