"""Allow negative adjustments without changing expense or income semantics."""

from alembic import op

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE transactions DROP CONSTRAINT transactions_amount_check")
    op.execute("""ALTER TABLE transactions ADD CONSTRAINT transactions_amount_check
        CHECK (amount > 0 OR (type = 'ADJUSTMENT' AND amount < 0))""")


def downgrade() -> None:
    # Fail atomically if signed adjustments exist; never erase financial history.
    op.execute("ALTER TABLE transactions DROP CONSTRAINT transactions_amount_check")
    op.execute("""ALTER TABLE transactions ADD CONSTRAINT transactions_amount_check
        CHECK (amount > 0)""")
