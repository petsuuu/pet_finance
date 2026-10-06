from typing import Annotated
from uuid import UUID

from pydantic import Field

from app.schemas.inputs import Input, Name


class MerchantSetup(Input):
    name: Name
    aliases: Annotated[list[Name], Field(max_length=50)] = []
    default_category_id: UUID | None = None
    notes: str | None = None


class MerchantPatch(Input):
    default_category_id: UUID | None = None
    notes: str | None = None
