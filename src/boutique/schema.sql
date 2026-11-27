CREATE SCHEMA IF NOT EXISTS boutique;

CREATE TABLE IF NOT EXISTS boutique.accounts (
    account_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    email TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('client', 'admin')),
    full_name TEXT NOT NULL,
    customer_unique_id CHAR(32) UNIQUE,
    city TEXT,
    state CHAR(2),
    postal_code CHAR(5),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (
        (role = 'client' AND customer_unique_id IS NOT NULL AND city IS NOT NULL
         AND state IS NOT NULL AND postal_code IS NOT NULL)
        OR role = 'admin'
    )
);

CREATE TABLE IF NOT EXISTS boutique.orders (
    order_id CHAR(32) PRIMARY KEY,
    account_id BIGINT NOT NULL REFERENCES boutique.accounts(account_id),
    created_at TIMESTAMPTZ NOT NULL,
    total NUMERIC(12,2) NOT NULL CHECK (total > 0),
    status TEXT NOT NULL DEFAULT 'processing'
        CHECK (status IN ('processing', 'approved', 'shipped', 'delivered', 'canceled')),
    csv_filename TEXT NOT NULL UNIQUE
);
CREATE INDEX IF NOT EXISTS ix_boutique_orders_account
    ON boutique.orders (account_id, created_at DESC);
