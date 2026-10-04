"""Apply supplied schema and seed a local user and Carteira."""

from pathlib import Path

from alembic import op
from sqlalchemy import text

from app.core.config import Settings

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    connection = op.get_bind()
    schema = Path(__file__).resolve().parents[2] / "schema.sql"
    connection.exec_driver_sql(schema.read_text())
    connection.execute(
        text("INSERT INTO app_users (id, name) VALUES (:id, 'Local user')"),
        {"id": Settings().user_id},
    )
    connection.execute(
        text("""INSERT INTO accounts (user_id, name, type)
                               VALUES (:id, 'Carteira', 'CASH')"""),
        {"id": Settings().user_id},
    )


def downgrade() -> None:
    tables = """import_row_map import_batches duplicate_candidates audit_log financial_goals
    budgets transaction_tags transactions installment_plans recurring_transactions tags
    categories accounts app_users""".split()
    op.execute("DROP VIEW IF EXISTS account_balances")
    for table in tables:
        op.execute(f"DROP TABLE IF EXISTS {table}")
    for name in [
        "source_type",
        "recurrence_frequency",
        "account_type",
        "expense_class",
        "transaction_status",
        "transaction_type",
    ]:
        op.execute(f"DROP TYPE IF EXISTS {name}")
