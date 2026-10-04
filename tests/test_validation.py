from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.schemas.inputs import TransactionCreate, TransactionPatch


def test_decimal_and_defaults() -> None:
    record = TransactionCreate(
        type="EXPENSE",
        description="Gasolina",
        amount="0.10",
        transaction_date="2026-10-04",
        account_id="00000000-0000-0000-0000-000000000001",
        idempotency_key="validation",
    )
    assert record.amount == Decimal("0.10")
    assert record.status == "PENDING"
    assert '"amount":"0.10"' in record.model_dump_json()


@pytest.mark.parametrize(
    "payload", [{"amount": None}, {"status": None}, {"amount": "-1"}, {"amount": "1.001"}]
)
def test_patch_rejects_invalid_values(payload: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        TransactionPatch.model_validate(payload)
