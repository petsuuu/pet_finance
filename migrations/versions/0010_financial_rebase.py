"""Keep prior finance data as an auditable archive and support dated opening balances."""

from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE accounts ADD COLUMN include_in_summary boolean NOT NULL DEFAULT true")
    op.execute(
        "ALTER TABLE transactions ADD COLUMN baseline_included boolean NOT NULL DEFAULT false"
    )
    for table in ("transactions", "recurring_transactions", "installment_plans", "budgets"):
        op.execute(f"ALTER TABLE {table} ADD COLUMN archived_at timestamptz")
    op.execute("""CREATE TABLE financial_rebases (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        user_id uuid NOT NULL REFERENCES app_users(id) ON DELETE CASCADE,
        batch_key varchar(120) NOT NULL,
        payload_hash char(64) NOT NULL,
        as_of_date date NOT NULL,
        archived_at timestamptz NOT NULL,
        result jsonb NOT NULL,
        created_at timestamptz NOT NULL DEFAULT now(),
        UNIQUE(user_id, batch_key)
    )""")
    op.execute("DROP VIEW account_balances")
    op.execute("""CREATE VIEW account_balances AS
    SELECT a.id AS account_id, a.user_id, a.name, a.active, a.include_in_summary,
           a.opening_balance_date,
           a.opening_balance + COALESCE(SUM(CASE
                WHEN t.archived_at IS NOT NULL OR t.status <> 'POSTED'
                  OR t.baseline_included = true
                  OR (a.opening_balance_date IS NOT NULL
                      AND t.transaction_date < a.opening_balance_date) THEN 0
                WHEN t.type IN ('INCOME','REFUND','YIELD') AND t.account_id = a.id THEN t.amount
                WHEN t.type IN ('EXPENSE','CARD_PAYMENT') AND t.account_id = a.id THEN -t.amount
                WHEN t.type = 'TRANSFER' AND t.account_id = a.id THEN -t.amount
                WHEN t.type = 'TRANSFER' AND t.target_account_id = a.id THEN t.amount
                WHEN t.type = 'ADJUSTMENT' AND t.account_id = a.id THEN t.amount
                ELSE 0 END), 0) AS current_balance
    FROM accounts a
    LEFT JOIN transactions t ON (t.account_id = a.id OR t.target_account_id = a.id)
    GROUP BY a.id, a.user_id, a.name, a.active, a.include_in_summary,
             a.opening_balance, a.opening_balance_date""")
    op.execute(
        "CREATE INDEX ix_finance_tx_live ON transactions(user_id, transaction_date) "
        "WHERE archived_at IS NULL"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_finance_tx_live")
    op.execute("DROP VIEW account_balances")
    op.execute("DROP TABLE financial_rebases")
    op.execute("ALTER TABLE transactions DROP COLUMN baseline_included")
    for table in ("transactions", "recurring_transactions", "installment_plans", "budgets"):
        op.execute(f"ALTER TABLE {table} DROP COLUMN archived_at")
    op.execute("ALTER TABLE accounts DROP COLUMN include_in_summary")
    op.execute("""CREATE VIEW account_balances AS
    SELECT a.id AS account_id, a.user_id, a.name, a.opening_balance
    + COALESCE(SUM(CASE
        WHEN t.status <> 'POSTED' THEN 0
        WHEN t.type IN ('INCOME','REFUND','YIELD') AND t.account_id = a.id THEN t.amount
        WHEN t.type IN ('EXPENSE','CARD_PAYMENT') AND t.account_id = a.id THEN -t.amount
        WHEN t.type = 'TRANSFER' AND t.account_id = a.id THEN -t.amount
        WHEN t.type = 'TRANSFER' AND t.target_account_id = a.id THEN t.amount
        WHEN t.type = 'ADJUSTMENT' AND t.account_id = a.id THEN t.amount
        ELSE 0 END), 0) AS current_balance
    FROM accounts a
    LEFT JOIN transactions t ON (t.account_id = a.id OR t.target_account_id = a.id)
    GROUP BY a.id, a.user_id, a.name, a.opening_balance""")
