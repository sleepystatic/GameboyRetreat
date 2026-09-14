-- ============================================================
-- Operations system - stage 1
-- Components (bulk, fungible) and donors (individual, priced separately)
--
-- Run in the Supabase SQL Editor. Safe to re-run.
--
-- NOTE: deliberately contains no functions, triggers or DO blocks. The SQL
-- Editor splits scripts on semicolons and mis-parses dollar-quoted bodies,
-- which silently aborts the rest of the script. Plain DDL only.
-- ============================================================

-- ------------------------------------------------------------
-- COMPONENTS - bought in bulk, one is as good as another
-- ------------------------------------------------------------

CREATE TABLE IF NOT EXISTS components (
    id           SERIAL PRIMARY KEY,
    sku          TEXT UNIQUE NOT NULL,
    name         TEXT NOT NULL,
    kind         TEXT NOT NULL,
    fits_models  TEXT[] DEFAULT '{}',
    reorder_at   INTEGER NOT NULL DEFAULT 5,
    supplier     TEXT,
    supplier_url TEXT,
    notes        TEXT,
    archived     BOOLEAN NOT NULL DEFAULT FALSE,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS component_receipts (
    id            SERIAL PRIMARY KEY,
    component_id  INTEGER NOT NULL REFERENCES components(id) ON DELETE CASCADE,
    qty           INTEGER NOT NULL CHECK (qty > 0),
    total_cost    NUMERIC(10,2) NOT NULL CHECK (total_cost >= 0),
    shipping_cost NUMERIC(10,2) NOT NULL DEFAULT 0,
    supplier      TEXT,
    reference     TEXT,
    received_at   DATE NOT NULL DEFAULT CURRENT_DATE,
    notes         TEXT,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Manual adjustments only: scrap, miscount corrections, build consumption.
-- Receipts are NOT duplicated here - on-hand is receipts plus adjustments, so
-- no trigger is needed and there is nothing to keep in sync.
CREATE TABLE IF NOT EXISTS component_movements (
    id           SERIAL PRIMARY KEY,
    component_id INTEGER NOT NULL REFERENCES components(id) ON DELETE CASCADE,
    delta        INTEGER NOT NULL,
    reason       TEXT NOT NULL,
    note         TEXT,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS component_movements_component_idx
    ON component_movements (component_id);
CREATE INDEX IF NOT EXISTS component_receipts_component_idx
    ON component_receipts (component_id);

-- ------------------------------------------------------------
-- DONORS - individual consoles, each with its own price
--
-- A donor bought in a bundle records the full price paid; anything else from
-- that purchase which gets sold is logged as a credit. Effective cost is the
-- difference, so a $60 console that came with a game sold for $20 costs $40.
-- ------------------------------------------------------------

CREATE TABLE IF NOT EXISTS donors (
    id             SERIAL PRIMARY KEY,
    sku            TEXT UNIQUE NOT NULL,
    model          TEXT NOT NULL,
    serial_no      TEXT,
    acquired_at    DATE NOT NULL DEFAULT CURRENT_DATE,
    acquired_from  TEXT,
    purchase_price NUMERIC(10,2) NOT NULL CHECK (purchase_price >= 0),
    condition_in   TEXT,
    status         TEXT NOT NULL DEFAULT 'available',
    notes          TEXT,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS donor_credits (
    id           SERIAL PRIMARY KEY,
    donor_id     INTEGER NOT NULL REFERENCES donors(id) ON DELETE CASCADE,
    description  TEXT NOT NULL,
    net_proceeds NUMERIC(10,2) NOT NULL,
    realised_at  DATE NOT NULL DEFAULT CURRENT_DATE,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS donor_credits_donor_idx ON donor_credits (donor_id);
CREATE INDEX IF NOT EXISTS donors_status_idx ON donors (status);

-- ------------------------------------------------------------
-- VIEWS
--
-- security_invoker = true makes a view run with the privileges of whoever
-- queries it, so RLS on the underlying tables still applies. Without it a view
-- runs as its owner and quietly bypasses RLS entirely.
-- ------------------------------------------------------------

CREATE OR REPLACE VIEW component_stock
WITH (security_invoker = true) AS
SELECT c.id AS component_id,
       c.sku,
       c.name,
       c.kind,
       c.reorder_at,
       (COALESCE((SELECT SUM(r.qty) FROM component_receipts r
                   WHERE r.component_id = c.id), 0)
      + COALESCE((SELECT SUM(m.delta) FROM component_movements m
                   WHERE m.component_id = c.id), 0))::INTEGER AS qty_on_hand
FROM components c
WHERE NOT c.archived;

CREATE OR REPLACE VIEW component_cost
WITH (security_invoker = true) AS
SELECT c.id AS component_id,
       c.sku,
       COALESCE(SUM(r.qty), 0)::INTEGER AS qty_received,
       COALESCE(SUM(r.total_cost + r.shipping_cost), 0) AS total_spent,
       CASE WHEN COALESCE(SUM(r.qty), 0) > 0
            THEN ROUND(SUM(r.total_cost + r.shipping_cost) / SUM(r.qty), 4)
            ELSE 0 END AS avg_unit_cost
FROM components c
LEFT JOIN component_receipts r ON r.component_id = c.id
GROUP BY c.id;

CREATE OR REPLACE VIEW component_overview
WITH (security_invoker = true) AS
SELECT s.component_id,
       s.sku,
       s.name,
       s.kind,
       s.qty_on_hand,
       s.reorder_at,
       (s.qty_on_hand <= s.reorder_at) AS needs_reorder,
       k.avg_unit_cost,
       ROUND(s.qty_on_hand * k.avg_unit_cost, 2) AS stock_value
FROM component_stock s
JOIN component_cost k ON k.component_id = s.component_id;

CREATE OR REPLACE VIEW donor_cost
WITH (security_invoker = true) AS
SELECT d.id AS donor_id,
       d.sku,
       d.model,
       d.status,
       d.acquired_at,
       d.purchase_price,
       COALESCE(SUM(dc.net_proceeds), 0) AS credits,
       (d.purchase_price - COALESCE(SUM(dc.net_proceeds), 0)) AS effective_cost,
       (CURRENT_DATE - d.acquired_at) AS days_held
FROM donors d
LEFT JOIN donor_credits dc ON dc.donor_id = d.id
GROUP BY d.id;

CREATE OR REPLACE VIEW capital_summary
WITH (security_invoker = true) AS
SELECT
    (SELECT COALESCE(SUM(stock_value), 0) FROM component_overview) AS component_value,
    (SELECT COALESCE(SUM(effective_cost), 0) FROM donor_cost
      WHERE status IN ('available', 'allocated'))                  AS donor_value,
    (SELECT COALESCE(SUM(stock_value), 0) FROM component_overview)
  + (SELECT COALESCE(SUM(effective_cost), 0) FROM donor_cost
      WHERE status IN ('available', 'allocated'))                  AS capital_tied_up,
    (SELECT COUNT(*) FROM donors WHERE status = 'available')       AS donors_available,
    (SELECT COUNT(*) FROM component_overview WHERE needs_reorder)  AS components_low;

-- ------------------------------------------------------------
-- ROW LEVEL SECURITY
--
-- The ops app uses the anon key, which ships in the browser and is public by
-- design. RLS is the only thing keeping supplier costs and margins private.
-- Enabling RLS without a policy denies everyone, so both halves matter.
-- ------------------------------------------------------------

ALTER TABLE components          ENABLE ROW LEVEL SECURITY;
ALTER TABLE component_receipts  ENABLE ROW LEVEL SECURITY;
ALTER TABLE component_movements ENABLE ROW LEVEL SECURITY;
ALTER TABLE donors              ENABLE ROW LEVEL SECURITY;
ALTER TABLE donor_credits       ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS components_authenticated ON components;
CREATE POLICY components_authenticated ON components
    FOR ALL TO authenticated USING (true) WITH CHECK (true);

DROP POLICY IF EXISTS component_receipts_authenticated ON component_receipts;
CREATE POLICY component_receipts_authenticated ON component_receipts
    FOR ALL TO authenticated USING (true) WITH CHECK (true);

DROP POLICY IF EXISTS component_movements_authenticated ON component_movements;
CREATE POLICY component_movements_authenticated ON component_movements
    FOR ALL TO authenticated USING (true) WITH CHECK (true);

DROP POLICY IF EXISTS donors_authenticated ON donors;
CREATE POLICY donors_authenticated ON donors
    FOR ALL TO authenticated USING (true) WITH CHECK (true);

DROP POLICY IF EXISTS donor_credits_authenticated ON donor_credits;
CREATE POLICY donor_credits_authenticated ON donor_credits
    FOR ALL TO authenticated USING (true) WITH CHECK (true);

-- Signed-in users only; anon gets nothing.
REVOKE ALL ON component_stock    FROM anon;
REVOKE ALL ON component_cost     FROM anon;
REVOKE ALL ON component_overview FROM anon;
REVOKE ALL ON donor_cost         FROM anon;
REVOKE ALL ON capital_summary    FROM anon;

GRANT SELECT ON component_stock    TO authenticated;
GRANT SELECT ON component_cost     TO authenticated;
GRANT SELECT ON component_overview TO authenticated;
GRANT SELECT ON donor_cost         TO authenticated;
GRANT SELECT ON capital_summary    TO authenticated;

-- PostgREST caches the schema; new views are invisible until it reloads.
NOTIFY pgrst, 'reload schema';
