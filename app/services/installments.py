"""Adopt explicitly numbered imported installments without rewriting financial history."""

from typing import Any

from fastapi import HTTPException
from sqlalchemy import select

from app.schemas.plans import InstallmentSetup
from app.services.ledger import Ledger
from app.services.recurrences import lock


def setup_installment(service: Ledger, body: InstallmentSetup) -> dict[str, Any]:
    lock(service)
    service.reference("accounts", body.account_id)
    service.reference("categories", body.category_id)
    identities = [link.transaction_id for link in body.links]
    numbers = [link.number for link in body.links]
    if len(set(identities)) != len(identities) or len(set(numbers)) != len(numbers):
        raise HTTPException(422, "Duplicate transaction or installment number")
    if any(not body.first_tracked_number <= n <= body.total_installments for n in numbers):
        raise HTTPException(422, "Installment number outside tracked range")
    rows = [service.get("transactions", identity) for identity in identities]
    linked = set()
    for row, link in zip(rows, body.links, strict=True):
        if row["type"] != "EXPENSE" or row["recurrence_id"]:
            raise HTTPException(422, "Only non-recurring expenses can be adopted")
        if row["account_id"] != body.account_id or row["category_id"] != body.category_id:
            raise HTTPException(422, "Account or category differs")
        if row["installment_plan_id"]:
            if row["installment_number"] != link.number:
                raise HTTPException(409, "Existing installment number differs")
            linked.add(row["installment_plan_id"])
    if len(linked) > 1:
        raise HTTPException(409, "Transactions belong to different plans")
    data = body.model_dump(exclude={"links", "preview"})
    # A partial imported history does not establish the original purchase total.
    data["total_amount"] = (
        body.installment_amount * body.total_installments
        if body.first_tracked_number == 1
        else None
    )
    if data["total_amount"] is not None and data["total_amount"] >= 10**12:
        raise HTTPException(422, "Total exceeds monetary limit")
    plan = service.get("installment_plans", next(iter(linked))) if linked else None
    if plan is None:
        table = service.table("installment_plans")
        found = (
            service.connection.execute(
                select(table).where(
                    table.c.user_id == service.user_id,
                    table.c.account_id == body.account_id,
                    table.c.description == body.description,
                )
            )
            .mappings()
            .first()
        )
        if found:
            plan = dict(found)
    if plan and (not plan["active"] or any(plan[k] != v for k, v in data.items())):
        raise HTTPException(409, "Existing plan differs from requested configuration")
    if plan:
        table = service.table("transactions")
        occupied = {
            r["installment_number"]: r["id"]
            for r in service.connection.execute(
                select(table).where(
                    table.c.user_id == service.user_id, table.c.installment_plan_id == plan["id"]
                )
            ).mappings()
        }
        if any(
            n in occupied and occupied[n] != identity
            for n, identity in zip(numbers, identities, strict=True)
        ):
            raise HTTPException(409, "Installment number already linked to another transaction")
    if not body.preview:
        plan = plan or service.create("installment_plans", data)
        for row, link in zip(rows, body.links, strict=True):
            if not row["installment_plan_id"]:
                service.patch(
                    "transactions",
                    row["id"],
                    {
                        "installment_plan_id": plan["id"],
                        "installment_number": link.number,
                    },
                )
    return {
        "preview": body.preview,
        "plan": plan,
        "configuration": data,
        "linked_count": len(rows),
        "preserved_transactions": rows,
    }
