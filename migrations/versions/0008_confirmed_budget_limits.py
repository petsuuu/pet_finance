"""Persist the personal deployment's explicitly confirmed monthly budget preferences."""

from alembic import op

from app.core.config import Settings
from app.services.budgets import BudgetSet, rows, set_budget
from app.services.ledger import Ledger

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    service = Ledger(op.get_bind(), Settings().user_id)
    # This one-time adjustment is authorized for October 2026. No financial records
    # or other owners are changed; new/empty ledgers have no matching existing caps.
    limits = {
        "Refeições fora": "140",
        "Viagens e Hospedagem": "0",
        "Eventos e Shows": "0",
        "Óculos e Visão": "0",
    }
    categories = {r["id"]: r for r in rows(service, "categories")}
    for budget in rows(service, "budgets"):
        category = categories.get(budget["category_id"])
        if (
            category
            and category["active"]
            and category["name"] in limits
            and (budget["year"], budget["month"]) == (2026, 10)
        ):
            set_budget(
                service,
                BudgetSet(
                    year=2026,
                    month=10,
                    category_id=category["id"],
                    limit_amount=limits[category["name"]],
                    repeat_monthly=True,
                ),
            )


def downgrade() -> None:
    # Keep confirmed user preferences and the audit trail on schema rollback.
    pass
