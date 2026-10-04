"""Idempotency payload and root-category uniqueness."""

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""CREATE TABLE idempotency_requests (
        user_id uuid NOT NULL REFERENCES app_users(id),
        key text NOT NULL, payload_hash text NOT NULL,
        transaction_id uuid NOT NULL REFERENCES transactions(id),
        PRIMARY KEY (user_id, key))""")
    op.execute("""CREATE UNIQUE INDEX ux_categories_root
        ON categories(user_id, name) WHERE parent_id IS NULL""")


def downgrade() -> None:
    op.execute("DROP INDEX ux_categories_root")
    op.execute("DROP TABLE idempotency_requests")
