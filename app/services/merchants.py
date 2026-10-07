"""Explicit merchant aliases; no fuzzy match silently assigns a category."""

import re
import unicodedata
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select

from app.schemas.merchants import MerchantLearn, MerchantPatch, MerchantSetup
from app.services.ledger import Ledger
from app.services.recurrences import lock


def normalize_name(name: str) -> str:
    text = unicodedata.normalize("NFKD", name.casefold())
    text = "".join(c for c in text if not unicodedata.combining(c))
    return " ".join(re.sub(r"[^\w]+", " ", text, flags=re.UNICODE).split())


def resolve_merchant(service: Ledger, raw_name: str) -> dict[str, Any]:
    aliases = service.table("merchant_aliases")
    identity = service.connection.execute(
        select(aliases.c.merchant_id).where(
            aliases.c.user_id == service.user_id,
            aliases.c.normalized_name == normalize_name(raw_name),
        )
    ).scalar_one_or_none()
    if identity is None:
        return {"matched": False, "merchant": None, "suggested_category_id": None}
    merchant = service.get("merchants", identity)
    if not merchant["active"]:
        return {"matched": False, "merchant": None, "suggested_category_id": None}
    category = merchant["default_category_id"]
    if category and not service.get("categories", category)["active"]:
        category = None
    return {"matched": True, "merchant": merchant, "suggested_category_id": category}


def setup_merchant(service: Ledger, body: MerchantSetup) -> dict[str, Any]:
    lock(service)
    service.reference("categories", body.default_category_id)
    names = {normalize_name(n): n for n in [body.name, *body.aliases]}
    if "" in names:
        raise HTTPException(422, "Merchant names must contain letters or digits")
    merchants = service.table("merchants")
    row = (
        service.connection.execute(
            select(merchants).where(
                merchants.c.user_id == service.user_id,
                merchants.c.normalized_name == normalize_name(body.name),
            )
        )
        .mappings()
        .first()
    )
    if row:
        merchant = dict(row)
        if merchant["default_category_id"] != body.default_category_id:
            raise HTTPException(409, "Use update_merchant to change the default category")
    else:
        merchant = service.create(
            "merchants",
            {
                "name": body.name,
                "normalized_name": normalize_name(body.name),
                "default_category_id": body.default_category_id,
                "notes": body.notes,
            },
        )
    aliases = service.table("merchant_aliases")
    for normalized, name in names.items():
        existing = (
            service.connection.execute(
                select(aliases).where(
                    aliases.c.user_id == service.user_id,
                    aliases.c.normalized_name == normalized,
                )
            )
            .mappings()
            .first()
        )
        if existing:
            if existing["merchant_id"] != merchant["id"]:
                raise HTTPException(409, "Alias already belongs to another merchant")
        else:
            service.create(
                "merchant_aliases",
                {
                    "merchant_id": merchant["id"],
                    "name": name,
                    "normalized_name": normalized,
                },
            )
    return {"merchant": merchant}


def merchants_router(dependency: Any) -> APIRouter:
    router = APIRouter(prefix="/api/v1/merchants")

    @router.get("")
    def listing(
        service: Ledger = Depends(dependency),
        limit: int = Query(100, ge=1, le=500),
        offset: int = Query(0, ge=0),
    ) -> list[dict[str, Any]]:
        table = service.table("merchants")
        return [
            dict(row)
            for row in service.connection.execute(
                select(table)
                .where(
                    table.c.user_id == service.user_id,
                )
                .order_by(table.c.name, table.c.id)
                .limit(limit)
                .offset(offset)
            ).mappings()
        ]

    @router.get("/resolve")
    def resolve(
        raw_name: str = Query(min_length=1, max_length=200), service: Ledger = Depends(dependency)
    ) -> dict[str, Any]:
        return resolve_merchant(service, raw_name)

    @router.post("/setup")
    def setup(body: MerchantSetup, service: Ledger = Depends(dependency)) -> dict[str, Any]:
        return setup_merchant(service, body)

    @router.post("/learn/{identity}")
    def learn(
        identity: UUID, body: MerchantLearn, service: Ledger = Depends(dependency)
    ) -> dict[str, Any]:
        lock(service)
        transaction = service.get("transactions", identity)
        if transaction["type"] != "EXPENSE" or transaction["status"] == "CANCELLED":
            raise HTTPException(422, "Learn only from a confirmed expense classification")
        if transaction["category_id"] is None:
            raise HTTPException(422, "Confirm a category before learning a merchant")
        return setup_merchant(
            service,
            MerchantSetup(
                name=body.name,
                aliases=body.aliases,
                default_category_id=transaction["category_id"],
                notes="Category explicitly confirmed by user for future transactions.",
            ),
        )

    @router.patch("/{identity}")
    def patch(
        identity: UUID, body: MerchantPatch, service: Ledger = Depends(dependency)
    ) -> dict[str, Any]:
        lock(service)
        service.reference("categories", body.default_category_id)
        return service.patch("merchants", identity, body.model_dump(exclude_unset=True))

    return router
