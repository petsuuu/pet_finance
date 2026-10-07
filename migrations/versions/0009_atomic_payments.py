"""Atomic payment replay records; financial history remains in the existing audit log."""

from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""CREATE TABLE payment_operations (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        user_id uuid NOT NULL REFERENCES app_users(id) ON DELETE CASCADE,
        key varchar(200) NOT NULL,
        payload_hash varchar(64) NOT NULL,
        obligation_id uuid NOT NULL REFERENCES transactions(id),
        payment_id uuid NOT NULL REFERENCES transactions(id),
        result jsonb NOT NULL,
        created_at timestamptz NOT NULL DEFAULT now(),
        updated_at timestamptz NOT NULL DEFAULT now(),
        UNIQUE(user_id, key)
    )""")


def downgrade() -> None:
    op.execute("DROP TABLE payment_operations")
