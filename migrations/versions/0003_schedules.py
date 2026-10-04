"""Unique scheduled installments and recurrence occurrences."""

from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""CREATE UNIQUE INDEX ux_installment_number ON transactions
        (user_id, installment_plan_id, installment_number)
        WHERE installment_plan_id IS NOT NULL""")
    op.execute("""CREATE UNIQUE INDEX ux_recurrence_date ON transactions
        (user_id, recurrence_id, transaction_date) WHERE recurrence_id IS NOT NULL""")


def downgrade() -> None:
    op.execute("DROP INDEX ux_recurrence_date")
    op.execute("DROP INDEX ux_installment_number")
