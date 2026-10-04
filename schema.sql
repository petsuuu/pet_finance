-- Pet Finance - PostgreSQL Schema v1
-- Target: PostgreSQL 15+
-- Recommended hosting: Supabase/Postgres
-- Timezone convention: timestamps stored in UTC, app displays America/Sao_Paulo.

CREATE EXTENSION IF NOT EXISTS "pgcrypto";

DO $$ BEGIN
  CREATE TYPE transaction_type AS ENUM (
    'EXPENSE','INCOME','TRANSFER','REFUND','ADJUSTMENT','YIELD','CARD_PAYMENT'
  );
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
  CREATE TYPE transaction_status AS ENUM ('PENDING','POSTED','CANCELLED');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
  CREATE TYPE expense_class AS ENUM ('ESSENTIAL','FUNDAMENTAL','SUPERFLUOUS');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
  CREATE TYPE account_type AS ENUM ('CHECKING','SAVINGS','CASH','INVESTMENT','CREDIT_CARD','OTHER');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
  CREATE TYPE recurrence_frequency AS ENUM ('DAILY','WEEKLY','MONTHLY','YEARLY');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
  CREATE TYPE source_type AS ENUM ('CHATGPT','MANUAL','IMPORT','RECURRENCE','SYSTEM','CLOFIN');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

CREATE TABLE IF NOT EXISTS app_users (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  name text NOT NULL,
  timezone text NOT NULL DEFAULT 'America/Sao_Paulo',
  currency_code char(3) NOT NULL DEFAULT 'BRL',
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS accounts (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id uuid NOT NULL REFERENCES app_users(id) ON DELETE CASCADE,
  name text NOT NULL,
  type account_type NOT NULL,
  currency_code char(3) NOT NULL DEFAULT 'BRL',
  opening_balance numeric(14,2) NOT NULL DEFAULT 0,
  opening_balance_date date,
  active boolean NOT NULL DEFAULT true,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE(user_id, name)
);

CREATE TABLE IF NOT EXISTS categories (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id uuid NOT NULL REFERENCES app_users(id) ON DELETE CASCADE,
  parent_id uuid REFERENCES categories(id) ON DELETE SET NULL,
  name text NOT NULL,
  expense_class expense_class,
  active boolean NOT NULL DEFAULT true,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE(user_id, parent_id, name)
);

CREATE TABLE IF NOT EXISTS tags (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id uuid NOT NULL REFERENCES app_users(id) ON DELETE CASCADE,
  name text NOT NULL,
  active boolean NOT NULL DEFAULT true,
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE(user_id, name)
);

CREATE TABLE IF NOT EXISTS recurring_transactions (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id uuid NOT NULL REFERENCES app_users(id) ON DELETE CASCADE,
  description text NOT NULL,
  type transaction_type NOT NULL,
  expected_amount numeric(14,2),
  tolerance_amount numeric(14,2) NOT NULL DEFAULT 0,
  account_id uuid REFERENCES accounts(id) ON DELETE SET NULL,
  category_id uuid REFERENCES categories(id) ON DELETE SET NULL,
  frequency recurrence_frequency NOT NULL,
  due_day smallint CHECK (due_day BETWEEN 1 AND 31),
  weekday smallint CHECK (weekday BETWEEN 0 AND 6),
  month_of_year smallint CHECK (month_of_year BETWEEN 1 AND 12),
  start_date date NOT NULL,
  end_date date,
  next_due_date date,
  active boolean NOT NULL DEFAULT true,
  notes text,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS installment_plans (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id uuid NOT NULL REFERENCES app_users(id) ON DELETE CASCADE,
  description text NOT NULL,
  total_amount numeric(14,2),
  installment_amount numeric(14,2) NOT NULL,
  total_installments integer NOT NULL CHECK (total_installments > 0),
  first_installment_date date NOT NULL,
  account_id uuid REFERENCES accounts(id) ON DELETE SET NULL,
  category_id uuid REFERENCES categories(id) ON DELETE SET NULL,
  active boolean NOT NULL DEFAULT true,
  notes text,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS transactions (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id uuid NOT NULL REFERENCES app_users(id) ON DELETE CASCADE,
  type transaction_type NOT NULL,
  status transaction_status NOT NULL DEFAULT 'PENDING',
  description text NOT NULL,
  amount numeric(14,2) NOT NULL CHECK (amount > 0),
  currency_code char(3) NOT NULL DEFAULT 'BRL',
  transaction_date date NOT NULL,
  account_id uuid REFERENCES accounts(id) ON DELETE SET NULL,
  target_account_id uuid REFERENCES accounts(id) ON DELETE SET NULL,
  category_id uuid REFERENCES categories(id) ON DELETE SET NULL,
  recurrence_id uuid REFERENCES recurring_transactions(id) ON DELETE SET NULL,
  installment_plan_id uuid REFERENCES installment_plans(id) ON DELETE SET NULL,
  installment_number integer CHECK (installment_number IS NULL OR installment_number > 0),
  source source_type NOT NULL DEFAULT 'MANUAL',
  external_id text,
  notes text,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  CHECK (
    type <> 'TRANSFER'
    OR (account_id IS NOT NULL AND target_account_id IS NOT NULL AND account_id <> target_account_id)
  )
);

CREATE UNIQUE INDEX IF NOT EXISTS ux_transactions_external_source
  ON transactions(user_id, source, external_id)
  WHERE external_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS ix_transactions_user_date
  ON transactions(user_id, transaction_date DESC);

CREATE INDEX IF NOT EXISTS ix_transactions_category
  ON transactions(user_id, category_id, transaction_date DESC);

CREATE INDEX IF NOT EXISTS ix_transactions_account
  ON transactions(user_id, account_id, transaction_date DESC);

CREATE INDEX IF NOT EXISTS ix_transactions_status
  ON transactions(user_id, status, transaction_date DESC);

CREATE TABLE IF NOT EXISTS transaction_tags (
  transaction_id uuid NOT NULL REFERENCES transactions(id) ON DELETE CASCADE,
  tag_id uuid NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
  PRIMARY KEY (transaction_id, tag_id)
);

CREATE TABLE IF NOT EXISTS budgets (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id uuid NOT NULL REFERENCES app_users(id) ON DELETE CASCADE,
  category_id uuid NOT NULL REFERENCES categories(id) ON DELETE CASCADE,
  year smallint NOT NULL CHECK (year BETWEEN 2000 AND 2100),
  month smallint NOT NULL CHECK (month BETWEEN 1 AND 12),
  limit_amount numeric(14,2) NOT NULL CHECK (limit_amount >= 0),
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE(user_id, category_id, year, month)
);

CREATE TABLE IF NOT EXISTS financial_goals (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id uuid NOT NULL REFERENCES app_users(id) ON DELETE CASCADE,
  name text NOT NULL,
  target_amount numeric(14,2),
  current_amount numeric(14,2) NOT NULL DEFAULT 0,
  target_date date,
  priority smallint CHECK (priority BETWEEN 1 AND 5),
  status text NOT NULL DEFAULT 'ACTIVE',
  notes text,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS audit_log (
  id bigserial PRIMARY KEY,
  user_id uuid NOT NULL REFERENCES app_users(id) ON DELETE CASCADE,
  entity_type text NOT NULL,
  entity_id uuid,
  action text NOT NULL,
  actor text NOT NULL,
  before_data jsonb,
  after_data jsonb,
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_audit_user_created
  ON audit_log(user_id, created_at DESC);

CREATE TABLE IF NOT EXISTS duplicate_candidates (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id uuid NOT NULL REFERENCES app_users(id) ON DELETE CASCADE,
  transaction_id uuid NOT NULL REFERENCES transactions(id) ON DELETE CASCADE,
  candidate_transaction_id uuid NOT NULL REFERENCES transactions(id) ON DELETE CASCADE,
  score numeric(5,4) NOT NULL CHECK (score BETWEEN 0 AND 1),
  reason jsonb,
  status text NOT NULL DEFAULT 'OPEN',
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE(transaction_id, candidate_transaction_id)
);

CREATE TABLE IF NOT EXISTS import_batches (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id uuid NOT NULL REFERENCES app_users(id) ON DELETE CASCADE,
  source source_type NOT NULL,
  filename text,
  imported_rows integer NOT NULL DEFAULT 0,
  rejected_rows integer NOT NULL DEFAULT 0,
  checksum text,
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS import_row_map (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  batch_id uuid NOT NULL REFERENCES import_batches(id) ON DELETE CASCADE,
  external_id text,
  transaction_id uuid REFERENCES transactions(id) ON DELETE SET NULL,
  raw_payload jsonb,
  created_at timestamptz NOT NULL DEFAULT now()
);

-- Recommended view for current balances.
CREATE OR REPLACE VIEW account_balances AS
SELECT
  a.id AS account_id,
  a.user_id,
  a.name,
  a.opening_balance
  + COALESCE(SUM(
      CASE
        WHEN t.status <> 'POSTED' THEN 0
        WHEN t.type IN ('INCOME','REFUND','YIELD') AND t.account_id = a.id THEN t.amount
        WHEN t.type IN ('EXPENSE','CARD_PAYMENT') AND t.account_id = a.id THEN -t.amount
        WHEN t.type = 'TRANSFER' AND t.account_id = a.id THEN -t.amount
        WHEN t.type = 'TRANSFER' AND t.target_account_id = a.id THEN t.amount
        WHEN t.type = 'ADJUSTMENT' AND t.account_id = a.id THEN t.amount
        ELSE 0
      END
  ), 0) AS current_balance
FROM accounts a
LEFT JOIN transactions t
  ON (t.account_id = a.id OR t.target_account_id = a.id)
GROUP BY a.id, a.user_id, a.name, a.opening_balance;