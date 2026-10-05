"""Persistent, hashed OAuth credentials for the single-owner MCP connection."""

from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""CREATE TABLE oauth_records (
        id text PRIMARY KEY,
        user_id uuid NOT NULL,
        kind text NOT NULL,
        payload jsonb NOT NULL,
        created_at timestamptz NOT NULL DEFAULT now(),
        expires_at timestamptz NOT NULL
    )""")
    op.execute("CREATE INDEX ix_oauth_expiry ON oauth_records (user_id, kind, expires_at)")


def downgrade() -> None:
    op.execute("DROP TABLE oauth_records")
