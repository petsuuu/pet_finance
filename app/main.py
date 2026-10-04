from collections.abc import Iterator
from datetime import date
from secrets import compare_digest
from typing import Annotated, Any
from uuid import UUID

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.responses import JSONResponse
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError

from app.core.config import Settings
from app.db.database import build_engine
from app.schemas.inputs import (
    AccountCreate,
    AccountPatch,
    CategoryCreate,
    CategoryPatch,
    Kind,
    Status,
    TransactionCreate,
    TransactionPatch,
)
from app.services.ledger import Ledger
from app.services.plans import plans_router


def create_app(settings: Settings) -> FastAPI:
    engine = build_engine(settings.database_url)
    api = FastAPI(title="Pet Finance", version="0.1.0")

    def authorize(authorization: Annotated[str | None, Header()] = None) -> None:
        if not settings.api_token or not compare_digest(
            authorization or "", f"Bearer {settings.api_token}"
        ):
            raise HTTPException(401, "Invalid token")

    def ledger(_: Annotated[None, Depends(authorize)]) -> Iterator[Ledger]:
        with engine.begin() as connection:
            yield Ledger(connection, settings.user_id)

    Service = Annotated[Ledger, Depends(ledger)]

    @api.exception_handler(IntegrityError)
    async def integrity_error(request: Any, exc: IntegrityError) -> JSONResponse:
        return JSONResponse(status_code=409, content={"detail": "Database constraint conflict"})

    @api.get("/health")
    def health() -> dict[str, str]:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        return {"status": "ok"}

    @api.get("/api/v1/accounts")
    def accounts(service: Service) -> list[dict[str, Any]]:
        return [
            dict(row)
            for row in service.connection.execute(
                text("SELECT * FROM account_balances WHERE user_id = :user ORDER BY name"),
                {"user": settings.user_id},
            ).mappings()
        ]

    @api.post("/api/v1/accounts", status_code=201)
    def create_account(body: AccountCreate, service: Service) -> dict[str, Any]:
        return service.create("accounts", body.model_dump())

    @api.patch("/api/v1/accounts/{identity}")
    def patch_account(identity: UUID, body: AccountPatch, service: Service) -> dict[str, Any]:
        data = body.model_dump(exclude_unset=True)
        if any(value is None for value in data.values()):
            raise HTTPException(422, "Fields cannot be null")
        return service.patch("accounts", identity, data)

    @api.get("/api/v1/categories")
    def categories(service: Service) -> list[dict[str, Any]]:
        table = service.table("categories")
        return [
            dict(row)
            for row in service.connection.execute(
                select(table).where(table.c.user_id == settings.user_id).order_by(table.c.name)
            ).mappings()
        ]

    @api.post("/api/v1/categories", status_code=201)
    def create_category(body: CategoryCreate, service: Service) -> dict[str, Any]:
        service.reference("categories", body.parent_id)
        return service.create("categories", body.model_dump())

    @api.patch("/api/v1/categories/{identity}")
    def patch_category(identity: UUID, body: CategoryPatch, service: Service) -> dict[str, Any]:
        data = body.model_dump(exclude_unset=True)
        if any(data.get(key) is None for key in body.model_fields_set & {"name", "active"}):
            raise HTTPException(422, "Fields cannot be null")
        return service.patch("categories", identity, data)

    @api.post("/api/v1/transactions", status_code=201)
    def create_transaction(body: TransactionCreate, service: Service) -> dict[str, Any]:
        return service.transaction(body)

    @api.get("/api/v1/transactions")
    def transactions(
        service: Service,
        start_date: date | None = None,
        end_date: date | None = None,
        account_id: UUID | None = None,
        category_id: UUID | None = None,
        status: Status | None = None,
        type: Kind | None = None,
        search: str | None = None,
        limit: Annotated[int, Query(ge=1, le=500)] = 100,
        offset: Annotated[int, Query(ge=0)] = 0,
    ) -> list[dict[str, Any]]:
        table = service.table("transactions")
        query = select(table).where(table.c.user_id == settings.user_id)
        for key, value in {
            "account_id": account_id,
            "category_id": category_id,
            "status": status,
            "type": type,
        }.items():
            if value is not None:
                query = query.where(table.c[key] == value)
        if start_date:
            query = query.where(table.c.transaction_date >= start_date)
        if end_date:
            query = query.where(table.c.transaction_date <= end_date)
        if search:
            query = query.where(table.c.description.icontains(search, autoescape=True))
        return [
            dict(row)
            for row in service.connection.execute(
                query.order_by(table.c.transaction_date.desc(), table.c.id)
                .limit(limit)
                .offset(offset)
            ).mappings()
        ]

    @api.get("/api/v1/transactions/{identity}")
    def get_transaction(identity: UUID, service: Service) -> dict[str, Any]:
        return service.get("transactions", identity)

    @api.patch("/api/v1/transactions/{identity}")
    def patch_transaction(
        identity: UUID, body: TransactionPatch, service: Service
    ) -> dict[str, Any]:
        if service.get("transactions", identity)["status"] == "CANCELLED":
            raise HTTPException(409, "Cancelled transactions cannot be edited")
        service.reference("categories", body.category_id)
        return service.patch("transactions", identity, body.model_dump(exclude_unset=True))

    @api.post("/api/v1/transactions/{identity}/cancel")
    def cancel_transaction(identity: UUID, service: Service) -> dict[str, Any]:
        before = service.get("transactions", identity)
        if before["status"] == "CANCELLED":
            return before
        return service.patch("transactions", identity, {"status": "CANCELLED"})

    @api.get("/api/v1/audit/transactions")
    def audit(
        service: Service, limit: Annotated[int, Query(ge=1, le=500)] = 100
    ) -> list[dict[str, Any]]:
        table = service.table("audit_log")
        return [
            dict(row)
            for row in service.connection.execute(
                select(table)
                .where(table.c.user_id == settings.user_id)
                .order_by(table.c.id.desc())
                .limit(limit)
            ).mappings()
        ]

    api.include_router(plans_router(ledger))
    return api


app = create_app(Settings())
