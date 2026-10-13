-- ============================================================
-- Seed: mod/repair services and lessons
--
-- Every row is inserted as status='coming_soon' ON PURPOSE. The prices below
-- are placeholders I picked to be roughly market-rate, NOT your numbers. In
-- that status a listing is visible but cannot be bought, so nothing can sell
-- at a price you did not choose.
--
-- To go live: /admin/listings -> Edit -> set your real price -> Availability
-- to "For sale".
--
-- Safe to re-run: ON CONFLICT on slug.
-- ============================================================

INSERT INTO inventory (category, title, description, price, stock, images, slug,
                       item_type, status)
VALUES

-- ---------- send-in services ----------
('mod-services',
 'Shell Replacement',
 'Send in your console and it comes back in a brand new shell, with new buttons and membranes. Fully disassembled, board cleaned, battery terminals treated for corrosion, then reassembled and tested. Choose your colourway when you book. Turnaround is typically 5-7 days from arrival, return shipping included.',
 60.00, 0, '[]'::jsonb, 'shell-replacement', 'service', 'coming_soon'),

('mod-services',
 'IPS Screen Install',
 'A modern backlit IPS panel fitted to your Game Boy Advance or SP. Dramatically brighter and sharper than the original screen, with adjustable brightness. Includes the panel, shell trimming where needed, and a full clean while it is open. Covered by the 30-day warranty.',
 145.00, 0, '[]'::jsonb, 'ips-screen-install', 'service', 'coming_soon'),

('mod-services',
 'USB-C Charging Port',
 'Replaces the original barrel or proprietary charge port with modern USB-C. Charge it with the same cable as everything else you own. Includes the board, soldering, and a charge cycle test before it ships back.',
 55.00, 0, '[]'::jsonb, 'usb-c-charging-port', 'service', 'coming_soon'),

('mod-services',
 'Full Restoration',
 'The works: new shell, buttons and membranes, IPS screen, USB-C charging, deep clean and any faults repaired. Send in the console you already own and get back something better than it left the factory. Best value if you want more than one upgrade.',
 240.00, 0, '[]'::jsonb, 'full-restoration', 'service', 'coming_soon'),

('mod-services',
 'Diagnostic & Repair',
 'Not sure what is wrong with it? Send it in and I will find out. You get a written diagnosis and a quote before any work happens, and the fee comes off the repair if you go ahead. Dead screens, no power, audio faults, corroded boards.',
 35.00, 0, '[]'::jsonb, 'diagnostic-and-repair', 'service', 'coming_soon'),

-- ---------- lessons ----------
('lessons',
 'One-to-One Session (1 hour)',
 'An hour over Zoom, screen shared both ways, working on whatever console you want to learn on. Shell swaps, backlight and IPS installs, USB-C conversions, board cleaning. I will tell you what tools to have ready beforehand. Booked by email within 24 hours of purchase.',
 60.00, 0, '[]'::jsonb, 'one-to-one-session-1-hour', 'lesson', 'coming_soon'),

('lessons',
 'Beginner Shell Swap Walkthrough',
 'The best mod to start with: no soldering, about an hour, and the result is dramatic. We do it together on your console, start to finish, so you finish the session with a working handheld rather than a box of parts.',
 45.00, 0, '[]'::jsonb, 'beginner-shell-swap-walkthrough', 'lesson', 'coming_soon'),

('lessons',
 'IPS Install Walkthrough (90 min)',
 'The mod people most often get wrong. Ribbon cables tear easily and the panel has to sit flush or the whole thing looks off. Ninety minutes, walked through on your own hardware, with the tricky parts slowed right down.',
 90.00, 0, '[]'::jsonb, 'ips-install-walkthrough-90-min', 'lesson', 'coming_soon')

ON CONFLICT (slug) DO NOTHING;

-- Check what landed, and confirm nothing is buyable yet:
--   SELECT slug, title, price, item_type, status FROM inventory
--   WHERE item_type IN ('service','lesson') ORDER BY category, price;
