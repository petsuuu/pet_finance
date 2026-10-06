"""MCP tools reuse the authenticated REST handlers and their ledger safeguards."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import date
from typing import Any
from urllib.parse import urlsplit
from uuid import UUID

import httpx
from fastapi import FastAPI
from mcp.server.auth.settings import AuthSettings
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations
from pydantic import AnyHttpUrl
from sqlalchemy import Engine

from app.core.config import Settings
from app.schemas.inputs import TransactionCreate, TransactionPatch
from app.schemas.merchants import MerchantPatch, MerchantSetup
from app.schemas.plans import (
    InstallmentSetup,
    RecurrenceCancel,
    RecurrenceGenerate,
    RecurrencePatch,
    RecurrenceSetup,
)
from app.services.budgets import BudgetGenerate, BudgetSet
from app.services.oauth import OwnerOAuth


def install_mcp(api: FastAPI, settings: Settings, engine: Engine) -> None:
    oauth = OwnerOAuth(settings, engine)
    host = urlsplit(oauth.issuer).netloc
    server = FastMCP(
        "Pet Finance",
        instructions="Use Pet Finance como base financeira. Consulte contas, categorias e "
        "lançamentos antes de gravar. Identifique pagamentos pendentes antes de atualizá-los. "
        "Mantenha a mesma idempotency_key ao repetir uma inclusão. Não repita gravações no CloFin. "
        "Ajuste de Saldo não é consumo. A projeção não inclui novos gastos variáveis.",
        stateless_http=True,
        json_response=True,
        token_verifier=oauth,
        auth=AuthSettings(
            issuer_url=AnyHttpUrl(oauth.issuer),
            resource_server_url=AnyHttpUrl(oauth.resource),
            required_scopes=["finance"],
            validate_token_resource=True,
        ),
        transport_security=TransportSecuritySettings(
            allowed_hosts=[host],
            allowed_origins=[oauth.issuer],
        ),
    )
    read = ToolAnnotations(readOnlyHint=True, destructiveHint=False, openWorldHint=False)
    write = ToolAnnotations(
        readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=False
    )
    meta = {"securitySchemes": [{"type": "oauth2", "scopes": ["finance"]}]}

    async def rest(
        method: str,
        path: str,
        body: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
    ) -> Any:
        # ASGI transport stays in-process. API_TOKEN is never sent to an external host.
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=api),
            base_url=oauth.issuer,
            headers={"Authorization": "Bearer " + settings.api_token},
        ) as client:
            response = await client.request(method, "/api/v1" + path, json=body, params=params)
        if response.is_error:
            raise ValueError(f"Pet Finance ({response.status_code}): {response.text}")
        return response.json()

    @server.tool(annotations=read, meta=meta)
    async def list_budgets(year: int, month: int) -> dict[str, Any]:
        """Consulte tetos por categoria, gastos, pendências, restante e alertas do mês.

        NEEDS_REVIEW indica ausência de evidência, não necessidade de gasto zero.
        O restante do orçamento não é saldo disponível; consulte a projeção de caixa.
        Cada categoria cobre lançamentos diretamente nela, sem somar filhos duas vezes.
        """
        result: dict[str, Any] = await rest(
            "GET", "/budgets", params={"year": str(year), "month": str(month)}
        )
        return result

    @server.tool(annotations=write, meta=meta)
    async def generate_budgets(body: BudgetGenerate) -> dict[str, Any]:
        """Calcule e cadastre tetos por histórico e obrigações, com prévia por padrão.

        preview=false aplica a autorização do usuário. Usa até três meses completos,
        separando férias identificadas, e protege compromissos registrados. Preserva tetos
        existentes e manuais; não aumenta limites por excesso de consumo. Sem histórico,
        sinaliza revisão. savings_target é meta, não transferência ou dinheiro recebido.
        Não confirma capacidade de gastar nem altera pagamentos.
        """
        result: dict[str, Any] = await rest(
            "POST", "/budgets/generate", body.model_dump(mode="json")
        )
        return result

    @server.tool(annotations=write, meta=meta)
    async def set_budget(body: BudgetSet) -> dict[str, Any]:
        """Defina o teto mensal de uma categoria ativa, conforme pedido do usuário.

        Consulte IDs e orçamento antes. Alterações manuais são preservadas pela geração
        automática. Não transfere dinheiro nem muda lançamentos.
        """
        result: dict[str, Any] = await rest("POST", "/budgets/set", body.model_dump(mode="json"))
        return result

    @server.tool(annotations=read, meta=meta)
    async def list_accounts() -> dict[str, Any]:
        """Consulte contas e saldos atuais. Use account_id retornado ao registrar gastos."""
        return {"items": await rest("GET", "/accounts")}

    @server.tool(annotations=read, meta=meta)
    async def list_categories() -> dict[str, Any]:
        """Consulte categorias. Use IDs existentes e ativos; não invente identificadores."""
        return {"items": await rest("GET", "/categories")}

    @server.tool(annotations=read, meta=meta)
    async def list_tags() -> dict[str, Any]:
        """Consulte tags existentes, incluindo Férias 2026, antes de usar seus IDs."""
        return {"items": await rest("GET", "/tags")}

    @server.tool(annotations=read, meta=meta)
    async def list_transactions(
        start_date: date | None = None,
        end_date: date | None = None,
        search: str | None = None,
        status: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> dict[str, Any]:
        """Localize lançamentos por período, descrição ou status, com paginação.

        Busque a conta pendente antes de marcar um pagamento, evitando criar outro gasto.
        """
        params = {k: str(v) for k, v in locals().items() if v is not None}
        return {"items": await rest("GET", "/transactions", params=params)}

    @server.tool(annotations=read, meta=meta)
    async def monthly_dashboard(
        year: int,
        month: int,
        as_of: date | None = None,
        safety_margin: str = "0",
    ) -> dict[str, Any]:
        """Dashboard mensal com compromissos, orçamento, plano de recuperação e comparação.

        category_comparison contém todas as categorias de despesa dos últimos três meses.
        Exiba paid_net por mês, identificando o atual como parcial. A variação usa o mesmo
        intervalo de dias (matched_period_net), não compare mês parcial com mês inteiro
        para afirmar economia. SEM_BASE_COMPARAVEL indica evidência insuficiente.
        Férias permanecem no consumo real, separadas em extraordinary_net. Gasto menor
        não prova poupança; preserve datas reais e não some categorias pai/filhas novamente.
        """
        params = {k: str(v) for k, v in locals().items() if v is not None}
        result: dict[str, Any] = await rest("GET", "/dashboard/monthly", params=params)
        return result

    @server.tool(annotations=read, meta=meta)
    async def monthly_agenda(year: int, month: int, as_of: date | None = None) -> dict[str, Any]:
        """Lista mensal de despesas pagas, pendentes e atrasadas, com recorrentes e parcelas.

        Use summary para uma visão curta e items para a lista completa com data e status.
        Inclui atrasos registrados de meses anteriores. recorded=false identifica uma estimativa
        sem lançamento: não a apresente como conta já registrada ou paga. Consulte
        missing_forecasts para auditoria. Não grava previsões nem confirma pagamentos.
        Datas dos registros são transaction_date; não invente vencimentos ou datas de pagamento.
        """
        params = {k: str(v) for k, v in locals().items() if v is not None}
        result: dict[str, Any] = await rest("GET", "/dashboard/agenda", params=params)
        return result

    @server.tool(annotations=read, meta=meta)
    async def daily_cashflow(as_of: date | None = None, safety_margin: str = "0") -> dict[str, Any]:
        """Projeção de saldo dia a dia até o fim do mês, com menor saldo e primeira data negativa.

        Receitas PENDING são estimativas, não dinheiro recebido. Pendências anteriores ao dia
        são consideradas no dia consultado. A projeção não inclui novos gastos variáveis,
        não confirma saldo bancário e não garante limite de gasto.
        """
        params = {k: str(v) for k, v in locals().items() if v is not None}
        result: dict[str, Any] = await rest("GET", "/dashboard/cashflow", params=params)
        return result

    @server.tool(annotations=read, meta=meta)
    async def recovery_plan(
        safety_margin: str = "0", estimated_bank_charges: str | None = None
    ) -> dict[str, Any]:
        """Plano para sair do negativo com datas, receitas, obrigações e cortes sugeridos.

        Não altera limites, pagamentos ou saldo. Juros desconhecidos ficam sinalizados;
        estimated_bank_charges é somente uma estimativa informada, não taxa inferida.
        Mostre data condicionada de recuperação com orçamento variável, compromissos
        preservados, redução necessária e quando a meta pode caber após receber as entradas.
        Não apresente previsão como quitação ou transferência de poupança. Data nula
        significa que o cenário não demonstrou recuperação até o fim do mês.
        """
        params = {k: str(v) for k, v in locals().items() if v is not None}
        result: dict[str, Any] = await rest("GET", "/dashboard/recovery-plan", params=params)
        return result

    @server.tool(annotations=read, meta=meta)
    async def check_purchase(
        amount: str, category_id: UUID | None = None, safety_margin: str = "0"
    ) -> dict[str, Any]:
        """Simule se uma compra paga hoje cabe no caixa, categoria e meta de poupança.

        Consulte a categoria ativa antes. Não registra a compra. Receitas previstas não
        são saldo hoje. NAO_RECOMENDADO indica caixa ou orçamento insuficiente; REVISAR
        indica categoria sem limite validado; CABE_NO_CENARIO é estimativa condicionada,
        não garantia bancária. Informe saldo após compra, motivos e próximos compromissos.
        A margem padrão zero não é uma reserva; respeite a margem indicada pelo usuário.
        """
        params = {k: str(v) for k, v in locals().items() if v is not None}
        result: dict[str, Any] = await rest("GET", "/dashboard/purchase-check", params=params)
        return result

    @server.tool(annotations=write, meta=meta)
    async def setup_installment(body: InstallmentSetup) -> dict[str, Any]:
        """Vincule parcelas existentes a um plano, com prévia por padrão, sem gerar lançamentos.

        Consulte todo o histórico e confirme os números a partir das descrições/notas.
        links associa cada transaction_id ao número original. first_installment_date é a
        data planejada da parcela 1; first_tracked_number limita o histórico comprovado.
        Não invente parcelas antigas. Preserve os valores reais, cancelamentos e pagamentos
        antecipados. Se não houver evidência suficiente para o calendário, esclareça primeiro.
        preview=false aplica a configuração confirmada; repetir não cria outro plano.
        """
        result: dict[str, Any] = await rest(
            "POST", "/installments/setup", body.model_dump(mode="json")
        )
        return result

    @server.tool(annotations=read, meta=meta)
    async def list_recurring_and_installments() -> dict[str, Any]:
        """Consulte regras e planos. Parcelas migradas do CloFin estão também em transactions."""
        return {
            "recurrences": await rest("GET", "/recurrences"),
            "installments": await rest("GET", "/installments"),
        }

    @server.tool(annotations=write, meta=meta)
    async def setup_recurrence(body: RecurrenceSetup) -> dict[str, Any]:
        """Configure recorrência mensal ou anual vinculando lançamentos existentes confirmados.

        Consulte lançamentos primeiro. Vincule apenas a mesma obrigação, um por mês de ocorrência;
        para YEARLY informe month_of_year e vincule somente o mês de renovação;
        preserve datas/valores pagos. Não inclua parcelamentos. Respeite limites de continuidade
        confirmados pelo usuário. Repetir o mesmo corpo não cria outra regra.
        """
        result: dict[str, Any] = await rest(
            "POST", "/recurrences/setup", body.model_dump(mode="json")
        )
        return result

    @server.tool(annotations=write, meta=meta)
    async def update_recurrence(identity: UUID, body: RecurrencePatch) -> dict[str, Any]:
        """Edite a regra existente após consultar list_recurring_and_installments.

        Use end_date para a continuidade confirmada e active=false para cancelar a regra.
        Preserva IDs, vínculos e lançamentos existentes; não cancela previsões já registradas.
        Valores alterados valem para gerações futuras, sem mudar pagamentos anteriores.
        Não crie outra regra para prorrogar. Não inclua parcelamentos.
        """
        result: dict[str, Any] = await rest(
            "PATCH", f"/recurrences/{identity}", body.model_dump(mode="json", exclude_unset=True)
        )
        return result

    @server.tool(annotations=write, meta=meta)
    async def generate_recurrence(identity: UUID, body: RecurrenceGenerate) -> dict[str, Any]:
        """Gere previsões PENDING no período confirmado, no máximo 366 dias.

        Meses já vinculados são preservados, inclusive pagamentos antecipados e cancelamentos.
        A geração é idempotente; não confirma pagamentos nem executa agendamento por si só.
        """
        result: dict[str, Any] = await rest(
            "POST", f"/recurrences/{identity}/generate", body.model_dump(mode="json")
        )
        return result

    @server.tool(annotations=write, meta=meta)
    async def cancel_recurrence(identity: UUID, body: RecurrenceCancel) -> dict[str, Any]:
        """Cancele uma recorrência a partir da data indicada, com prévia por padrão.

        Consulte regras antes. preview=true mostra pendências e alterações sem gravar.
        Após o usuário confirmar o cancelamento concreto, use preview=false com a mesma data.
        Cancela apenas PENDING vinculados desde a data; preserva pagos, atrasos anteriores,
        IDs e auditoria. Não usar para parcelamentos. A regra termina antes da data indicada.
        """
        result: dict[str, Any] = await rest(
            "POST", f"/recurrences/{identity}/cancel", body.model_dump(mode="json")
        )
        return result

    @server.tool(annotations=read, meta=meta)
    async def list_merchants(limit: int = 100, offset: int = 0) -> dict[str, Any]:
        """Consulte estabelecimentos cadastrados e categorias habituais, com paginação."""
        return {"items": await rest("GET", "/merchants", params={"limit": limit, "offset": offset})}

    @server.tool(annotations=read, meta=meta)
    async def resolve_merchant(raw_name: str) -> dict[str, Any]:
        """Localize o nome do cartão/Pix por alias cadastrado, sem adivinhar por similaridade.

        Categoria habitual é sugestão: o produto comprado pode exigir outra categoria.
        Sem correspondência, pesquise apenas nome e cidade ou pergunte ao usuário.
        Nunca deduza o vendedor a partir de uma intermediadora genérica.
        """
        result: dict[str, Any] = await rest(
            "GET", "/merchants/resolve", params={"raw_name": raw_name}
        )
        return result

    @server.tool(annotations=write, meta=meta)
    async def setup_merchant(body: MerchantSetup) -> dict[str, Any]:
        """Cadastre nome conhecido e aliases confirmados do cartão/Pix.

        Consulte resolve_merchant e categorias primeiro. Repetição não duplica o cadastro.
        Não cadastre intermediadoras genéricas como aliases de um único vendedor.
        Não altera lançamentos antigos; novos gastos podem usar merchant_id retornado.
        """
        result: dict[str, Any] = await rest(
            "POST", "/merchants/setup", body.model_dump(mode="json")
        )
        return result

    @server.tool(annotations=write, meta=meta)
    async def update_merchant(identity: UUID, body: MerchantPatch) -> dict[str, Any]:
        """Altere a categoria habitual confirmada; lançamentos antigos permanecem iguais."""
        result: dict[str, Any] = await rest(
            "PATCH", f"/merchants/{identity}", body.model_dump(mode="json", exclude_unset=True)
        )
        return result

    @server.tool(annotations=write, meta=meta)
    async def create_transaction(body: TransactionCreate) -> dict[str, Any]:
        """Registre receita ou despesa solicitada pelo usuário, com categoria e conta verificadas.

        Reuse idempotency_key em tentativas da mesma operação. Se houver aviso de duplicidade,
        consulte o candidato e esclareça com o usuário. Não use force.
        Para estabelecimento confirmado, informe merchant_id obtido por resolve_merchant.
        Em despesas sem category_id usa a categoria habitual ativa; category_id explícito
        prevalece quando o produto comprado exige outra classificação.
        """
        if body.force:
            raise ValueError("MCP does not permit bypassing duplicate checks")
        payload = body.model_copy(update={"source": "CHATGPT"}).model_dump(mode="json")
        result: dict[str, Any] = await rest("POST", "/transactions", payload)
        return result

    @server.tool(annotations=write, meta=meta)
    async def update_transaction(identity: UUID, body: TransactionPatch) -> dict[str, Any]:
        """Atualize o lançamento identificado pelo usuário. POSTED significa pago/recebido.

        Para pagar uma conta existente, consulte o lançamento e atualize status, valor real
        e transaction_date. Não crie outra transação para o mesmo pagamento.
        """
        result: dict[str, Any] = await rest(
            "PATCH", f"/transactions/{identity}", body.model_dump(mode="json", exclude_unset=True)
        )
        return result

    app = server.streamable_http_app()
    api.include_router(oauth.router())
    api.mount("/", app)

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        async with server.session_manager.run():
            yield
        engine.dispose()

    api.router.lifespan_context = lifespan
