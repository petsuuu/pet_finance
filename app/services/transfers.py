"""Atomic internal transfers using the existing transaction ledger."""

import hashlib
from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Any
from uuid import UUID
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from pydantic import Field
from sqlalchemy import insert, select

from app.schemas.inputs import Input, Money, Name
from app.services.ledger import Ledger
from app.services.recurrences import lock


class TransferCreate(Input):
    account_id: UUID
    target_account_id: UUID
    amount: Annotated[Money, Field(gt=0)]
    transaction_date: date
    description: Name
    idempotency_key: Annotated[str, Field(min_length=1, max_length=200)]


def transfer(service: Ledger, body: TransferCreate) -> dict[str, Any]:
    lock(service)
    digest = hashlib.sha256(
        ("transfer:v1:" + body.model_dump_json()).encode()
    ).hexdigest()
    requests = service.table("idempotency_requests")
    prior = service.connection.execute(
        select(requests).where(
            requests.c.user_id == service.user_id,
            requests.c.key == body.idempotency_key,
        )
    ).mappings().first()
    if prior:
        if prior["payload_hash"] != digest:
            raise HTTPException(409, "Idempotency key already used with another payload")
        return service.get("transactions", prior["transaction_id"])
    if body.account_id == body.target_account_id:
        raise HTTPException(422, "Transfer accounts must differ")
    if body.transaction_date > datetime.now(ZoneInfo("America/Sao_Paulo")).date():
        raise HTTPException(422, "Future posted transfer")
    accounts = [
        service.get("accounts", identity)
        for identity in sorted([body.account_id, body.target_account_id])
    ]
    for account in accounts:
        if not account["active"] or account["type"] == "CREDIT_CARD":
            raise HTTPException(422, "Unsupported or inactive account")
        cutoff = account.get("opening_balance_date")
        if cutoff and body.transaction_date < cutoff:
            raise HTTPException(409, "Transfer precedes opening balance cutoff")
    if accounts[0]["currency_code"] != accounts[1]["currency_code"]:
        raise HTTPException(422, "Currency mismatch")
    table = service.table("transactions")
    duplicate = service.connection.execute(
        select(table.c.id).where(
            table.c.user_id == service.user_id,
            table.c.type == "TRANSFER",
            table.c.status == "POSTED",
            table.c.archived_at.is_(None),
            table.c.account_id == body.account_id,
            table.c.target_account_id == body.target_account_id,
            table.c.amount == body.amount,
            table.c.transaction_date == body.transaction_date,
        )
    ).first()
    if duplicate:
        raise HTTPException(409, "Matching transfer already exists; review before retrying")
    balances = service.table("account_balances")
    balance = service.connection.execute(
        select(balances.c.current_balance).where(
            balances.c.user_id == service.user_id,
            balances.c.account_id == body.account_id,
        )
    ).scalar_one()
    if Decimal(balance) < body.amount:
        raise HTTPException(409, "Insufficient recorded source balance")
    result = service.create(
        "transactions",
        {
            **body.model_dump(exclude={"idempotency_key"}),
            "type": "TRANSFER",
            "status": "POSTED",
            "source": "CHATGPT",
            "currency_code": accounts[0]["currency_code"],
        },
    )
    service.connection.execute(
        insert(requests).values(
            user_id=service.user_id,
            key=body.idempotency_key,
            payload_hash=digest,
            transaction_id=result["id"],
        )
    )
    return result
