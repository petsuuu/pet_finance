from datetime import date
from decimal import Decimal
from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field, model_validator

from app.schemas.inputs import Input, Kind, Money, Name


class RecurrenceCreate(Input):
    description: Name
    type: Kind = "EXPENSE"
    expected_amount: Annotated[Money, Field(gt=0)]
    tolerance_amount: Annotated[Money, Field(ge=0)] = Decimal("0")
    account_id: UUID
    category_id: UUID | None = None
    frequency: Literal["MONTHLY"] = "MONTHLY"
    due_day: Annotated[int, Field(ge=1, le=31)]
    start_date: date
    end_date: date | None = None
    notes: str | None = None

    @model_validator(mode="after")
    def dates(self) -> "RecurrenceCreate":
        if self.end_date and self.end_date < self.start_date:
            raise ValueError("end_date precedes start_date")
        return self


class RecurrencePatch(Input):
    description: Name | None = None
    expected_amount: Annotated[Money, Field(gt=0)] | None = None
    tolerance_amount: Annotated[Money, Field(ge=0)] | None = None
    due_day: Annotated[int, Field(ge=1, le=31)] | None = None
    end_date: date | None = None
    active: bool | None = None
    notes: str | None = None

    @model_validator(mode="after")
    def required(self) -> "RecurrencePatch":
        for key in self.model_fields_set - {"end_date", "notes"}:
            if getattr(self, key) is None:
                raise ValueError(f"{key} cannot be null")
        return self


class RecurrenceSetup(RecurrenceCreate):
    transaction_ids: Annotated[list[UUID], Field(min_length=1, max_length=120)]


class RecurrenceGenerate(Input):
    start_date: date
    end_date: date


class InstallmentCreate(Input):
    description: Name
    installment_amount: Annotated[Money, Field(gt=0)]
    total_installments: Annotated[int, Field(ge=1, le=600)]
    first_installment_date: date
    account_id: UUID
    category_id: UUID | None = None
    notes: str | None = None
