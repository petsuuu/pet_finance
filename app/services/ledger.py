import hashlib
import json
from difflib import SequenceMatcher
from typing import Any
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import Connection, MetaData, Table, insert, select, text, update

from app.schemas.inputs import TransactionCreate


class Ledger:
    def __init__(self, connection: Connection, user_id: UUID):
        self.connection = connection
        self.user_id = user_id
        self.metadata = MetaData()

    def table(self, name: str) -> Table:
        return Table(name, self.metadata, autoload_with=self.connection)

    def get(self, name: str, identity: UUID) -> dict[str, Any]:
        table = self.table(name)
        row = (
            self.connection.execute(
                select(table)
                .where(table.c.id == identity, table.c.user_id == self.user_id)
                .with_for_update()
            )
            .mappings()
            .first()
        )
        if row is None:
            raise HTTPException(404, "Resource not found")
        return dict(row)

    def audit(
        self,
        name: str,
        identity: UUID,
        action: str,
        before: dict[str, Any] | None,
        after: dict[str, Any],
    ) -> None:
        def serial(value: Any) -> Any:
            return json.loads(json.dumps(value, default=str))

        self.connection.execute(
            insert(self.table("audit_log")).values(
                user_id=self.user_id,
                entity_type=name,
                entity_id=identity,
                action=action,
                actor="API",
                before_data=serial(before),
                after_data=serial(after),
            )
        )

    def create(self, name: str, data: dict[str, Any]) -> dict[str, Any]:
        table = self.table(name)
        result = dict(
            self.connection.execute(
                insert(table).values(**data, user_id=self.user_id).returning(table)
            )
            .mappings()
            .one()
        )
        self.audit(name, result["id"], "CREATE", None, result)
        return result

    def patch(self, name: str, identity: UUID, data: dict[str, Any]) -> dict[str, Any]:
        before = self.get(name, identity)
        table = self.table(name)
        result = dict(
            self.connection.execute(
                update(table)
                .where(table.c.id == identity, table.c.user_id == self.user_id)
                .values(**data, updated_at=text("now()"))
                .returning(table)
            )
            .mappings()
            .one()
        )
        self.audit(
            name,
            identity,
            "CANCEL" if data.get("status") == "CANCELLED" else "UPDATE",
            before,
            result,
        )
        return result

    def reference(self, name: str, identity: UUID | None) -> None:
        if identity is not None:
            if not self.get(name, identity)["active"]:
                raise HTTPException(422, "Inactive reference")

    def transaction(self, payload: TransactionCreate) -> dict[str, Any]:
        # Serialize writes per user: idempotency and duplicate checks are atomic even
        # when separate workers receive simultaneous requests.
        self.connection.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:u, 0))"), {"u": str(self.user_id)}
        )
        data = payload.model_dump(exclude={"idempotency_key", "force", "tags"})
        digest = hashlib.sha256(payload.model_dump_json(exclude={"force"}).encode()).hexdigest()
        requests = self.table("idempotency_requests")
        prior = (
            self.connection.execute(
                select(requests).where(
                    requests.c.user_id == self.user_id, requests.c.key == payload.idempotency_key
                )
            )
            .mappings()
            .first()
        )
        if prior:
            if prior["payload_hash"] != digest:
                raise HTTPException(409, "Idempotency key already used with another payload")
            return self.get("transactions", prior["transaction_id"])
        self.reference("accounts", payload.account_id)
        self.reference("categories", payload.category_id)
        self.reference("recurring_transactions", payload.recurrence_id)
        if payload.recurrence_id:
            rule = self.get("recurring_transactions", payload.recurrence_id)
            if rule["account_id"] != payload.account_id or rule["type"] != payload.type:
                raise HTTPException(422, "Recurrence account or type mismatch")
        for tag_id in payload.tags:
            self.reference("tags", tag_id)
        transactions = self.table("transactions")
        matches = self.connection.execute(
            select(transactions).where(
                transactions.c.user_id == self.user_id,
                transactions.c.status != "CANCELLED",
                transactions.c.type == payload.type,
                transactions.c.amount == payload.amount,
                transactions.c.transaction_date == payload.transaction_date,
            )
        ).mappings()
        warning = False
        for candidate in matches:
            similarity = SequenceMatcher(
                None, payload.description.casefold(), candidate["description"].casefold()
            ).ratio()
            score = 0.65 + 0.2 * similarity
            score += 0.1 if candidate["category_id"] == payload.category_id else 0
            score += 0.05 if candidate["account_id"] == payload.account_id else 0
            warning = warning or score >= 0.75
            if score >= 0.90 and not payload.force:
                raise HTTPException(
                    409,
                    {
                        "duplicate_warning": True,
                        "candidate_id": str(candidate["id"]),
                        "score": score,
                    },
                )
        result = self.create("transactions", data)
        for tag_id in set(payload.tags):
            self.connection.execute(
                insert(self.table("transaction_tags")).values(
                    transaction_id=result["id"], tag_id=tag_id
                )
            )
        if payload.tags:
            self.audit("transaction_tags", result["id"], "CREATE", None, {"tags": payload.tags})
        self.connection.execute(
            insert(requests).values(
                user_id=self.user_id,
                key=payload.idempotency_key,
                payload_hash=digest,
                transaction_id=result["id"],
            )
        )
        return {**result, "duplicate_warning": warning}
