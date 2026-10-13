-- ============================================================
-- Security hardening
--
-- Supabase grants the public `anon` role full table privileges on everything in
-- the public schema by default, and does NOT enable RLS. Any table that misses
-- both is readable AND writable by anyone holding the anon key - which is
-- public by design, since it ships in the browser.
--
-- Two tables were in that state: `subscribers` and `reservations`. They read as
-- empty only because no one has subscribed or checked out yet; the moment they
-- have rows, those rows are exposed.
--
-- Nothing here affects the storefront: Flask connects over Postgres directly as
-- the table owner, which bypasses RLS entirely.
-- ============================================================

-- ------------------------------------------------------------
-- 1. RLS on the two tables that were missing it.
--    No policy is added, which means deny-all through the REST API. That is
--    correct - nothing should reach these except the Flask app.
-- ------------------------------------------------------------

ALTER TABLE subscribers  ENABLE ROW LEVEL SECURITY;
ALTER TABLE reservations ENABLE ROW LEVEL SECURITY;

-- ------------------------------------------------------------
-- 2. Revoke the anon role everywhere in public.
--    Nothing legitimately uses the REST API anonymously: the storefront talks
--    to Postgres directly, and the ops app always signs in first. RLS already
--    blocks anon, but removing the grant as well means a future table created
--    without RLS is not instantly exposed.
-- ------------------------------------------------------------

REVOKE ALL ON ALL TABLES    IN SCHEMA public FROM anon;
REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM anon;
REVOKE ALL ON ALL FUNCTIONS IN SCHEMA public FROM anon;

-- Stop the same default from re-applying to tables created later.
ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE ALL ON TABLES    FROM anon;
ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE ALL ON SEQUENCES FROM anon;
ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE ALL ON FUNCTIONS FROM anon;

-- ------------------------------------------------------------
-- 3. Re-grant exactly what the ops app needs, to signed-in users only.
--    RLS still gates the rows; these grants only make the objects visible.
-- ------------------------------------------------------------

GRANT SELECT, INSERT, UPDATE, DELETE ON components          TO authenticated;
GRANT SELECT, INSERT, UPDATE, DELETE ON component_receipts  TO authenticated;
GRANT SELECT, INSERT, UPDATE, DELETE ON component_movements TO authenticated;
GRANT SELECT, INSERT, UPDATE, DELETE ON donors              TO authenticated;
GRANT SELECT, INSERT, UPDATE, DELETE ON donor_credits       TO authenticated;

GRANT USAGE, SELECT ON SEQUENCE components_id_seq          TO authenticated;
GRANT USAGE, SELECT ON SEQUENCE component_receipts_id_seq  TO authenticated;
GRANT USAGE, SELECT ON SEQUENCE component_movements_id_seq TO authenticated;
GRANT USAGE, SELECT ON SEQUENCE donors_id_seq              TO authenticated;
GRANT USAGE, SELECT ON SEQUENCE donor_credits_id_seq       TO authenticated;

GRANT SELECT ON component_stock    TO authenticated;
GRANT SELECT ON component_cost     TO authenticated;
GRANT SELECT ON component_overview TO authenticated;
GRANT SELECT ON donor_cost         TO authenticated;
GRANT SELECT ON capital_summary    TO authenticated;

-- ------------------------------------------------------------
-- 4. Storefront tables: authenticated has no business reading these through
--    the REST API either. RLS already denies, but be explicit.
-- ------------------------------------------------------------

REVOKE ALL ON orders       FROM authenticated;
REVOKE ALL ON sellers      FROM authenticated;
REVOKE ALL ON subscribers  FROM authenticated;
REVOKE ALL ON reservations FROM authenticated;
REVOKE ALL ON inventory    FROM authenticated;

NOTIFY pgrst, 'reload schema';
