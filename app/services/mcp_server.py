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
from app.schemas.plans import RecurrenceGenerate, RecurrenceSetup
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
        """Saldo, resultado do mês, compromissos e projeção sem estimativa de novos gastos."""
        params = {k: str(v) for k, v in locals().items() if v is not None}
        result: dict[str, Any] = await rest("GET", "/dashboard/monthly", params=params)
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
        """Configure uma recorrência mensal e vincule IDs de lançamentos existentes confirmados.

        Consulte lançamentos primeiro. Vincule apenas a mesma obrigação, um por mês;
        preserve datas/valores pagos. Não inclua parcelamentos. Respeite limites de continuidade
        confirmados pelo usuário. Repetir o mesmo corpo não cria outra regra.
        """
        result: dict[str, Any] = await rest(
            "POST", "/recurrences/setup", body.model_dump(mode="json")
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
    async def create_transaction(body: TransactionCreate) -> dict[str, Any]:
        """Registre receita ou despesa solicitada pelo usuário, com categoria e conta verificadas.

        Reuse idempotency_key em tentativas da mesma operação. Se houver aviso de duplicidade,
        consulte o candidato e esclareça com o usuário. Não use force.
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
