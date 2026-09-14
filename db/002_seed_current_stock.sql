-- ============================================================
-- Seed: current stock as of Sept 2026
--
-- Review before running. Re-running is safe (ON CONFLICT DO NOTHING on the
-- components), but the RECEIPTS are not guarded - running this twice would
-- double-count the shells. Run once.
--
-- CHECK BEFORE RUNNING: fits_models is a guess. Confirm whether these shells
-- are AGS-001 (front-lit) or AGS-101 (backlit) or both.
-- ============================================================

INSERT INTO components (sku, name, kind, fits_models, reorder_at, supplier, notes)
VALUES
  ('SHELL-RAYQ', 'Rayquaza shell', 'shell', ARRAY['AGS-001','AGS-101'], 5,
   'Alibaba', 'Part of the 60-unit bulk order, Aug 2026'),
  ('SHELL-GROU', 'Groudon shell',  'shell', ARRAY['AGS-001','AGS-101'], 5,
   'Alibaba', 'Part of the 60-unit bulk order, Aug 2026'),
  ('SHELL-KYOG', 'Kyogre shell',   'shell', ARRAY['AGS-001','AGS-101'], 5,
   'Alibaba', 'Part of the 60-unit bulk order, Aug 2026')
ON CONFLICT (sku) DO NOTHING;

-- The bulk order: 60 shells, $390 all-in including freight and fees = $6.50 each.
-- Split evenly across the three colorways, 20 each at $130.
-- Freight is already inside total_cost, so shipping_cost stays 0 to avoid
-- double-counting it.
INSERT INTO component_receipts
    (component_id, qty, total_cost, shipping_cost, supplier, reference, received_at, notes)
SELECT id, 20, 130.00, 0, 'Alibaba', 'BULK-2026-08', DATE '2026-08-15',
       'Freight and fees included in total_cost ($390 / 60 units = $6.50 each)'
FROM components
WHERE sku IN ('SHELL-RAYQ', 'SHELL-GROU', 'SHELL-KYOG');


-- ------------------------------------------------------------
-- Example donor, showing the bundle-credit case.
-- Bought a console for $60 that came with a game; the game sold for $20 net,
-- so the console's effective cost is $40.
--
-- Uncomment and edit, or add it through the ops app instead.
-- ------------------------------------------------------------

-- INSERT INTO donors (sku, model, acquired_at, acquired_from, purchase_price,
--                     condition_in, status, notes)
-- VALUES ('DNR-0001', 'AGS-001', DATE '2026-09-05', 'FB Marketplace', 60.00,
--         'Working, scuffed shell, hinge loose', 'available',
--         'Came bundled with Pokemon Emerald');
--
-- INSERT INTO donor_credits (donor_id, description, net_proceeds, realised_at)
-- SELECT id, 'Pokemon Emerald - sold on Mercari, net of fees', 20.00, DATE '2026-09-09'
-- FROM donors WHERE sku = 'DNR-0001';


-- Verify:
--   SELECT * FROM component_overview ORDER BY sku;
--   SELECT * FROM donor_cost ORDER BY sku;
--   SELECT * FROM capital_summary;
