"""Do not invent historical installments absent from an imported ledger."""

from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE installment_plans ADD COLUMN first_tracked_number integer "
        "NOT NULL DEFAULT 1 CHECK (first_tracked_number BETWEEN 1 AND total_installments)"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE installment_plans DROP COLUMN first_tracked_number")
