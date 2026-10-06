"""Expose category budgets and initialize the configured personal ledger's requested policy."""

from datetime import datetime
from zoneinfo import ZoneInfo

from alembic import op
from sqlalchemy import text

from app.core.config import Settings

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE budgets ADD COLUMN method varchar(16) NOT NULL DEFAULT 'MANUAL'")
    op.execute("ALTER TABLE budgets ADD COLUMN basis jsonb NOT NULL DEFAULT '{}'::jsonb")
    op.execute("""CREATE TABLE budget_preferences (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        user_id uuid NOT NULL UNIQUE REFERENCES app_users(id) ON DELETE CASCADE,
        savings_target numeric(14,2) NOT NULL CHECK(savings_target >= 0),
        enabled boolean NOT NULL DEFAULT true,
        created_at timestamptz NOT NULL DEFAULT now(),
        updated_at timestamptz NOT NULL DEFAULT now()
    )""")
    # One-time operation authorized for this personal Financeiro deployment. Empty test/new
    # ledgers and other owners are not initialized. Existing budgets are never overwritten.
    connection = op.get_bind()
    owner = Settings().user_id
    exists = connection.execute(
        text("SELECT 1 FROM transactions WHERE user_id=:owner LIMIT 1"), {"owner": owner}
    ).first()
    if exists:
        from app.services.budgets import BudgetGenerate, generate_budgets
        from app.services.ledger import Ledger

        today = datetime.now(ZoneInfo("America/Sao_Paulo")).date()
        generate_budgets(
            Ledger(connection, owner),
            BudgetGenerate(
                year=today.year,
                month=today.month,
                savings_target="500",
                preview=False,
            ),
        )


def downgrade() -> None:
    # Keep budget amounts and their audit trail; schema rollback does not delete plans.
    op.execute("DROP TABLE budget_preferences")
    op.execute("ALTER TABLE budgets DROP COLUMN basis")
    op.execute("ALTER TABLE budgets DROP COLUMN method")
