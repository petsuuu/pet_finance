"""Reconcile recorded balances against explicitly confirmed bank balances."""

import hashlib
import json
from datetime import datetime
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


class ReconciliationPreview(Input):
    account_id: UUID
    observed_balance: Money
    reason: Name


class ReconciliationCommit(ReconciliationPreview):
    expected_balance: Money
    preview_token: Annotated[str, Field(min_length=64, max_length=64)]
    idempotency_key: Annotated[str, Field(min_length=1, max_length=200)]


def preview(service: Ledger, body: ReconciliationPreview) -> dict[str, Any]:
    lock(service)
    account = service.get("accounts", body.account_id)
    if not account["active"] or account["type"] == "CREDIT_CARD":
        raise HTTPException(422, "Unsupported or inactive account")
    balances = service.table("account_balances")
    recorded = Decimal(service.connection.execute(
        select(balances.c.current_balance).where(
            balances.c.user_id == service.user_id,
            balances.c.account_id == body.account_id,
        )
    ).scalar_one())
    difference = body.observed_balance - recorded
    token = hashlib.sha256(json.dumps({
        "user": str(service.user_id),
        "account": str(body.account_id),
        "recorded": str(recorded.quantize(Decimal("0.01"))),
        "observed": str(body.observed_balance.quantize(Decimal("0.01"))),
        "reason": body.reason,
        "cutoff": str(account.get("opening_balance_date")),
    }, sort_keys=True).encode()).hexdigest()
    return {
        "account_id": body.account_id,
        "recorded_balance": recorded,
        "observed_balance": body.observed_balance,
        "difference": difference,
        "preview_token": token,
        "can_commit": difference > 0,
    }


def commit(service: Ledger, body: ReconciliationCommit) -> dict[str, Any]:
    lock(service)
    digest = hashlib.sha256(
        ("reconciliation:v1:" + body.model_dump_json()).encode()
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
    current = preview(service, body)
    if (
        current["recorded_balance"] != body.expected_balance
        or current["preview_token"] != body.preview_token
    ):
        raise HTTPException(409, "Balance changed; request a new preview")
    difference = current["difference"]
    if difference <= 0:
        raise HTTPException(422, "Zero or negative adjustment is not supported yet")
    today = datetime.now(ZoneInfo("America/Sao_Paulo")).date()
    account = service.get("accounts", body.account_id)
    cutoff = account.get("opening_balance_date")
    if cutoff and today < cutoff:
        raise HTTPException(409, "Adjustment precedes opening balance cutoff")
    result = service.create("transactions", {
        "account_id": body.account_id,
        "type": "ADJUSTMENT",
        "status": "POSTED",
        "description": "Balance reconciliation",
        "amount": difference,
        "transaction_date": today,
        "source": "CHATGPT",
        "notes": body.reason,
        "currency_code": account["currency_code"],
    })
    service.audit("transactions", result["id"], "RECONCILE", {
        "balance": body.expected_balance,
    }, {"balance": body.observed_balance, "reason": body.reason})
    service.connection.execute(insert(requests).values(
        user_id=service.user_id,
        key=body.idempotency_key,
        payload_hash=digest,
        transaction_id=result["id"],
    ))
    return result
