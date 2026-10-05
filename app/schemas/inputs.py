from datetime import date
from decimal import Decimal
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

Money = Annotated[Decimal, Field(max_digits=14, decimal_places=2, allow_inf_nan=False)]
Name = Annotated[str, Field(min_length=1, max_length=200)]
Kind = Literal["EXPENSE", "INCOME", "REFUND", "YIELD"]
Status = Literal["PENDING", "POSTED", "CANCELLED"]


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class AccountCreate(Input):
    name: Name
    type: Literal["CHECKING", "SAVINGS", "CASH", "INVESTMENT", "OTHER"] = "CASH"
    currency_code: Literal["BRL"] = "BRL"
    opening_balance: Money = Decimal("0")
    opening_balance_date: date | None = None


class AccountPatch(Input):
    name: Name | None = None
    active: bool | None = None


class CategoryCreate(Input):
    name: Name
    parent_id: UUID | None = None
    expense_class: Literal["ESSENTIAL", "FUNDAMENTAL", "SUPERFLUOUS"] | None = None


class CategoryPatch(Input):
    name: Name | None = None
    expense_class: Literal["ESSENTIAL", "FUNDAMENTAL", "SUPERFLUOUS"] | None = None
    active: bool | None = None


class TransactionCreate(Input):
    type: Kind
    status: Literal["PENDING", "POSTED"] = "PENDING"
    description: Name
    amount: Annotated[Money, Field(gt=0)]
    transaction_date: date
    account_id: UUID
    category_id: UUID | None = None
    source: Literal["CHATGPT", "MANUAL", "SYSTEM"] = "MANUAL"
    notes: str | None = None
    idempotency_key: Annotated[str, Field(min_length=1, max_length=200)]
    tags: list[UUID] = Field(default_factory=list, max_length=100)
    recurrence_id: UUID | None = None
    merchant_id: UUID | None = None
    force: bool = False


class TransactionPatch(Input):
    description: Name | None = None
    amount: Annotated[Money, Field(gt=0)] | None = None
    transaction_date: date | None = None
    status: Literal["PENDING", "POSTED"] | None = None
    category_id: UUID | None = None
    notes: str | None = None

    @model_validator(mode="after")
    def nonnull_required(self) -> "TransactionPatch":
        for key in self.model_fields_set - {"category_id", "notes"}:
            if getattr(self, key) is None:
                raise ValueError(f"{key} cannot be null")
        return self
