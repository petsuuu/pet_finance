"""Owner-scoped merchant names and payment aliases; existing transactions stay unchanged."""

from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""CREATE TABLE merchants (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        user_id uuid NOT NULL REFERENCES app_users(id),
        name varchar(200) NOT NULL,
        normalized_name text NOT NULL,
        default_category_id uuid REFERENCES categories(id),
        notes text,
        active boolean NOT NULL DEFAULT true,
        created_at timestamptz NOT NULL DEFAULT now(),
        updated_at timestamptz NOT NULL DEFAULT now(),
        UNIQUE(user_id, normalized_name)
    )""")
    op.execute("""CREATE TABLE merchant_aliases (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        user_id uuid NOT NULL REFERENCES app_users(id),
        merchant_id uuid NOT NULL REFERENCES merchants(id),
        name varchar(200) NOT NULL,
        normalized_name text NOT NULL,
        created_at timestamptz NOT NULL DEFAULT now(),
        updated_at timestamptz NOT NULL DEFAULT now(),
        UNIQUE(user_id, normalized_name)
    )""")
    op.execute("ALTER TABLE transactions ADD COLUMN merchant_id uuid REFERENCES merchants(id)")


def downgrade() -> None:
    op.execute("ALTER TABLE transactions DROP COLUMN merchant_id")
    op.execute("DROP TABLE merchant_aliases")
    op.execute("DROP TABLE merchants")
