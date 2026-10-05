"""Service behavior on an isolated database; PostgreSQL locking is covered by CI tests."""

from collections.abc import Iterator
from datetime import date, datetime
from typing import Any
from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    Date,
    DateTime,
    Integer,
    MetaData,
    Numeric,
    String,
    Table,
    Uuid,
    create_engine,
    insert,
    select,
)

from app.schemas.inputs import TransactionCreate
from app.schemas.merchants import MerchantSetup
from app.schemas.plans import RecurrenceCancel
from app.services.ledger import Ledger
from app.services.merchants import normalize_name, resolve_merchant, setup_merchant
from app.services.recurrences import cancel_recurrence


@pytest.fixture()
def memory_ledger(monkeypatch: pytest.MonkeyPatch) -> Iterator[Ledger]:
    engine = create_engine("sqlite://")
    metadata = MetaData()

    def table(name: str, *columns: Column[Any]) -> Table:
        return Table(
            name,
            metadata,
            Column("id", Uuid, primary_key=True, default=uuid4),
            Column("user_id", Uuid, nullable=False),
            Column("created_at", DateTime, default=datetime.now),
            Column("updated_at", DateTime, default=datetime.now),
            *columns,
        )

    table("accounts", Column("active", Boolean, default=True))
    table("categories", Column("active", Boolean, default=True))
    table(
        "merchants",
        Column("name", String),
        Column("normalized_name", String),
        Column("default_category_id", Uuid),
        Column("notes", String),
        Column("active", Boolean, default=True),
    )
    table(
        "merchant_aliases",
        Column("name", String),
        Column("normalized_name", String),
        Column("merchant_id", Uuid),
    )
    table(
        "recurring_transactions",
        Column("start_date", Date),
        Column("end_date", Date),
        Column("next_due_date", Date),
        Column("frequency", String),
        Column("due_day", Integer),
        Column("month_of_year", Integer),
        Column("active", Boolean, default=True),
    )
    table(
        "transactions",
        Column("status", String),
        Column("type", String),
        Column("description", String),
        Column("amount", Numeric(14, 2)),
        Column("transaction_date", Date),
        Column("account_id", Uuid),
        Column("category_id", Uuid),
        Column("recurrence_id", Uuid),
        Column("merchant_id", Uuid),
        Column("source", String),
        Column("notes", String),
    )
    table(
        "audit_log",
        Column("entity_type", String),
        Column("entity_id", Uuid),
        Column("action", String),
        Column("actor", String),
        Column("before_data", JSON),
        Column("after_data", JSON),
    )
    table(
        "idempotency_requests",
        Column("key", String),
        Column("payload_hash", String),
        Column("transaction_id", Uuid),
    )
    metadata.create_all(engine)
    with engine.begin() as connection:
        raw = connection.connection.driver_connection
        assert raw is not None
        raw.create_function("pg_advisory_xact_lock", 1, lambda _: 0)
        raw.create_function("hashtextextended", 2, lambda _value, _seed: 0)
        raw.create_function("now", 0, lambda: datetime.now().isoformat(" "))
        service = Ledger(connection, uuid4())
        monkeypatch.setattr(service, "table", lambda name: metadata.tables[name])
        yield service
    engine.dispose()


def rule(service: Ledger, **changes: Any) -> UUID:
    result = service.create(
        "recurring_transactions",
        {
            "start_date": date(2026, 1, 1),
            "end_date": date(2028, 12, 31),
            "next_due_date": date(2026, 1, 9),
            "frequency": "MONTHLY",
            "due_day": 9,
            "active": True,
            **changes,
        },
    )["id"]
    assert isinstance(result, UUID)
    return result


def pending(service: Ledger, identity: UUID | None, day: date, status: str = "PENDING") -> UUID:
    result = service.create(
        "transactions",
        {
            "recurrence_id": identity,
            "transaction_date": day,
            "status": status,
        },
    )["id"]
    assert isinstance(result, UUID)
    return result


def test_cancel_preview_preserves_paid_overdue_and_unlinked(memory_ledger: Ledger) -> None:
    service = memory_ledger
    identity = rule(service)
    old = pending(service, identity, date(2026, 9, 9))
    paid = pending(service, identity, date(2026, 11, 9), "POSTED")
    future = pending(service, identity, date(2026, 10, 9))
    unrelated = pending(service, None, date(2026, 10, 9))
    body = RecurrenceCancel(effective_date=date(2026, 10, 1))
    preview = cancel_recurrence(service, identity, body)
    assert preview["affected_count"] == 1
    assert service.get("transactions", future)["status"] == "PENDING"
    assert service.get("recurring_transactions", identity)["end_date"] == date(2028, 12, 31)
    applied = cancel_recurrence(service, identity, body.model_copy(update={"preview": False}))
    assert applied["cancelled_count"] == 1
    assert applied["transactions"][0]["status"] == "CANCELLED"
    assert service.get("transactions", paid)["status"] == "POSTED"
    assert service.get("transactions", old)["status"] == "PENDING"
    assert service.get("transactions", unrelated)["status"] == "PENDING"
    assert service.get("recurring_transactions", identity)["end_date"] == date(2026, 9, 30)
    assert (
        cancel_recurrence(service, identity, body.model_copy(update={"preview": False}))[
            "cancelled_count"
        ]
        == 0
    )


def test_cancel_annual_before_start_and_owner_isolation(memory_ledger: Ledger) -> None:
    service = memory_ledger
    identity = rule(
        service,
        frequency="YEARLY",
        month_of_year=8,
        start_date=date(2027, 8, 1),
        next_due_date=date(2027, 8, 9),
    )
    pending(service, identity, date(2027, 8, 9))
    body = RecurrenceCancel(effective_date=date(2027, 1, 1), preview=False)
    assert cancel_recurrence(service, identity, body)["recurrence"]["active"] is False
    service.user_id = uuid4()
    with pytest.raises(HTTPException) as error:
        cancel_recurrence(service, identity, body)
    assert error.value.status_code == 404


def test_merchant_aliases_idempotency_and_unknown_names(memory_ledger: Ledger) -> None:
    service = memory_ledger
    category = service.create("categories", {})["id"]
    body = MerchantSetup(
        name="Example Café", aliases=["EXAMPLE*CAFE LTDA"], default_category_id=category
    )
    first = setup_merchant(service, body)["merchant"]
    assert setup_merchant(service, body)["merchant"]["id"] == first["id"]
    assert normalize_name("  EXAMPLE*Café LTDA ") == "example cafe ltda"
    resolved = resolve_merchant(service, "Example Café LTDA")
    assert resolved["merchant"]["id"] == first["id"]
    assert resolved["suggested_category_id"] == category
    assert resolve_merchant(service, "Example Cafeteria LTDA")["matched"] is False
    assert resolve_merchant(service, "PAGSEGURO")["matched"] is False
    service.patch("categories", category, {"active": False})
    assert resolve_merchant(service, "Example Café LTDA")["suggested_category_id"] is None


def test_alias_collision_is_atomic_and_category_changes_are_explicit(memory_ledger: Ledger) -> None:
    service = memory_ledger
    setup_merchant(service, MerchantSetup(name="First", aliases=["CARD-NAME"]))
    with pytest.raises(HTTPException) as error, service.connection.begin_nested():
        setup_merchant(service, MerchantSetup(name="Second", aliases=["CARD-NAME"]))
    assert error.value.status_code == 409
    table = service.table("merchants")
    assert len(list(service.connection.execute(select(table)))) == 1
    category = service.create("categories", {})["id"]
    with pytest.raises(HTTPException):
        setup_merchant(service, MerchantSetup(name="First", default_category_id=category))
    service.user_id = uuid4()
    assert resolve_merchant(service, "CARD-NAME")["matched"] is False


def test_category_default_override_and_retries_preserve_old_transactions(
    memory_ledger: Ledger,
) -> None:
    service = memory_ledger
    account = service.create("accounts", {})["id"]
    first = service.create("categories", {})["id"]
    second = service.create("categories", {})["id"]
    merchant = setup_merchant(service, MerchantSetup(name="Shop", default_category_id=first))[
        "merchant"
    ]["id"]
    payload = TransactionCreate(
        type="EXPENSE",
        status="POSTED",
        description="Lunch",
        amount="33.25",
        transaction_date=date(2026, 10, 5),
        account_id=account,
        merchant_id=merchant,
        idempotency_key="lunch",
    )
    old = service.transaction(payload)
    assert old["category_id"] == first
    service.patch("merchants", merchant, {"default_category_id": second})
    assert service.transaction(payload)["id"] == old["id"]
    assert service.get("transactions", old["id"])["category_id"] == first
    explicit = service.transaction(
        payload.model_copy(
            update={
                "description": "Different purchase",
                "category_id": first,
                "idempotency_key": "other",
            }
        )
    )
    assert explicit["category_id"] == first
    income = service.transaction(
        payload.model_copy(
            update={
                "type": "INCOME",
                "description": "Refunded deposit",
                "idempotency_key": "income",
            }
        )
    )
    assert income["category_id"] is None


def test_old_idempotency_hash_survives_optional_merchant_field(memory_ledger: Ledger) -> None:
    import hashlib

    service = memory_ledger
    account = service.create("accounts", {})["id"]
    payload = TransactionCreate(
        type="EXPENSE",
        description="Old request",
        amount="10",
        transaction_date=date(2026, 10, 5),
        account_id=account,
        idempotency_key="old",
    )
    old = pending(service, None, date(2026, 10, 5))
    digest = hashlib.sha256(payload.model_dump_json(exclude={"force", "merchant_id"}).encode())
    service.connection.execute(
        insert(service.table("idempotency_requests")).values(
            user_id=service.user_id,
            key="old",
            payload_hash=digest.hexdigest(),
            transaction_id=old,
        )
    )
    assert service.transaction(payload)["id"] == old
