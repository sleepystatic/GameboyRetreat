# Operations System — Design

Inventory, build, and order management for the console business. Separate front-end,
**same Supabase database** as the storefront, so there is one source of truth.

Structural model: `newwaveInventoryDemo` (login / dashboard / inventory / product /
sales orders / purchasing).

---

## 1. The model: build-to-order assembly

This is **not** one-of-a-kind flipping. A finished console is a *configuration*
assembled from components held in bulk:

```
donor console  +  shell  +  [optional IPS screen]  +  [optional USB-C port]  +  labour
                                   = a finished, sold unit
```

Everything follows from that:

- **Availability is a buildable count**, not a shelf count. "How many Rayquaza SPs can
  I ship?" = min(donor SPs on hand, Rayquaza shells on hand, and for any chosen
  option, that option's parts on hand).
- **Selling the same config on four platforms is fine.** Component stock is the real
  constraint, so multi-channel listing is an advantage, not a double-sale risk. The
  system's job is to warn when outstanding orders exceed what can be built.
- **Cost is composed, not fixed.** A bulk order of 60 shells is an asset amortised
  across 60 builds, not an expense on the day it landed.
- **Bottlenecks become visible.** 60 shells and 3 donor consoles means donor sourcing
  is the constraint. That is the number worth watching.

---

## 2. Schema

```sql
-- ---------- components held in bulk ----------

CREATE TABLE components (
    id            SERIAL PRIMARY KEY,
    sku           TEXT UNIQUE NOT NULL,       -- SHELL-RAYQ, IPS-V2-SP, USBC-SP
    name          TEXT NOT NULL,              -- 'Rayquaza shell (AGS-001)'
    kind          TEXT NOT NULL,              -- donor|shell|screen|port|buttons|misc
    fits_models   TEXT[] DEFAULT '{}',        -- {'AGS-001','AGS-101'}
    qty_on_hand   INTEGER NOT NULL DEFAULT 0,
    reorder_at    INTEGER DEFAULT 5,
    avg_unit_cost NUMERIC(10,2) DEFAULT 0,    -- recomputed on each receipt
    supplier      TEXT,
    supplier_url  TEXT,
    notes         TEXT,
    created_at    TIMESTAMP DEFAULT NOW()
);

-- Every purchase of components. avg_unit_cost is derived from these, so a bulk
-- buy at a better price correctly lowers the cost basis of future builds.
CREATE TABLE component_receipts (
    id            SERIAL PRIMARY KEY,
    component_id  INTEGER NOT NULL REFERENCES components(id) ON DELETE CASCADE,
    qty           INTEGER NOT NULL,
    total_cost    NUMERIC(10,2) NOT NULL,     -- what the whole batch cost
    shipping_cost NUMERIC(10,2) DEFAULT 0,
    supplier      TEXT,
    received_at   DATE DEFAULT CURRENT_DATE,
    notes         TEXT
);

-- Consumption, so on-hand is always reconstructable and never just "trust me".
CREATE TABLE component_movements (
    id           SERIAL PRIMARY KEY,
    component_id INTEGER NOT NULL REFERENCES components(id) ON DELETE CASCADE,
    build_id     INTEGER REFERENCES builds(id) ON DELETE SET NULL,
    delta        INTEGER NOT NULL,            -- +received, -consumed, -scrapped
    reason       TEXT NOT NULL,               -- receipt|build|scrap|correction
    created_at   TIMESTAMP DEFAULT NOW()
);

-- ---------- what can be sold ----------

-- A sellable configuration. Prices the base; options priced separately.
CREATE TABLE configs (
    id            SERIAL PRIMARY KEY,
    listing_id    INTEGER REFERENCES inventory(id) ON DELETE SET NULL,
    slug          TEXT UNIQUE NOT NULL,
    name          TEXT NOT NULL,              -- 'Rayquaza AGS-001'
    model         TEXT NOT NULL,
    base_price    NUMERIC(10,2) NOT NULL,
    est_minutes   INTEGER DEFAULT 120,
    active        BOOLEAN DEFAULT TRUE
);

-- Bill of materials: what a config always consumes.
CREATE TABLE config_components (
    config_id    INTEGER NOT NULL REFERENCES configs(id) ON DELETE CASCADE,
    component_id INTEGER NOT NULL REFERENCES components(id),
    qty          INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY (config_id, component_id)
);

-- Optional extras: IPS screen, USB-C port. Each has its own price and parts.
CREATE TABLE options (
    id            SERIAL PRIMARY KEY,
    slug          TEXT UNIQUE NOT NULL,       -- 'ips-screen', 'usb-c'
    name          TEXT NOT NULL,
    description   TEXT,
    price         NUMERIC(10,2) NOT NULL,
    add_minutes   INTEGER DEFAULT 30,
    component_id  INTEGER REFERENCES components(id),   -- part it consumes
    fits_models   TEXT[] DEFAULT '{}',
    active        BOOLEAN DEFAULT TRUE
);

-- ---------- work ----------

-- One physical console being built, for stock or for a customer.
CREATE TABLE builds (
    id             SERIAL PRIMARY KEY,
    sku            TEXT UNIQUE NOT NULL,      -- BLD-0142
    config_id      INTEGER REFERENCES configs(id),
    kind           TEXT NOT NULL DEFAULT 'stock',  -- stock | customer_send_in
    status         TEXT NOT NULL DEFAULT 'queued',
    customer_email TEXT,                      -- send-in work
    order_id       INTEGER REFERENCES orders(id),
    labour_minutes INTEGER DEFAULT 0,
    sold_price     NUMERIC(10,2),
    sold_channel   TEXT,                      -- site|mercari|offerup|facebook
    sold_at        TIMESTAMP,
    fees           NUMERIC(10,2) DEFAULT 0,
    shipping_out   NUMERIC(10,2) DEFAULT 0,
    notes          TEXT,
    created_at     TIMESTAMP DEFAULT NOW(),
    updated_at     TIMESTAMP DEFAULT NOW()
);

CREATE TABLE build_options (
    build_id  INTEGER NOT NULL REFERENCES builds(id) ON DELETE CASCADE,
    option_id INTEGER NOT NULL REFERENCES options(id),
    price     NUMERIC(10,2) NOT NULL,   -- snapshot: price at time of sale
    PRIMARY KEY (build_id, option_id)
);

-- Immutable audit trail. Never change status without writing one.
CREATE TABLE build_events (
    id         SERIAL PRIMARY KEY,
    build_id   INTEGER NOT NULL REFERENCES builds(id) ON DELETE CASCADE,
    from_state TEXT,
    to_state   TEXT NOT NULL,
    note       TEXT,
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE TABLE shipments (
    id           SERIAL PRIMARY KEY,
    build_id     INTEGER REFERENCES builds(id),
    channel      TEXT,
    buyer_name   TEXT,
    address      JSONB,
    carrier      TEXT,
    tracking_no  TEXT,
    label_cost   NUMERIC(10,2),
    status       TEXT DEFAULT 'awaiting',   -- awaiting|packed|shipped|delivered
    shipped_at   TIMESTAMP,
    delivered_at TIMESTAMP
);
```

### Buildable count

The number the storefront and the dashboard both need:

```sql
CREATE OR REPLACE VIEW config_buildable AS
SELECT c.id AS config_id,
       c.slug,
       COALESCE(MIN(comp.qty_on_hand / NULLIF(cc.qty, 0)), 0) AS buildable
FROM configs c
JOIN config_components cc ON cc.config_id = c.id
JOIN components comp      ON comp.id = cc.component_id
WHERE c.active
GROUP BY c.id, c.slug;
```

`inventory.stock` on the storefront becomes this number, minus builds already
committed to orders. Shop availability then reflects manufacturing reality with no
manual stock-keeping at all.

### Build lifecycle

```
queued -> in_progress -> awaiting_parts -> qc -> ready -> sold -> shipped -> delivered
              ^                                   |
              +-----------------------------------+   (fails QC)
```

Send-in work adds `received` at the front and skips `sold`.

---

## 3. Cost and profit

Per build:

```
COGS   = Σ (component avg_unit_cost × qty consumed)
         + Σ (option component costs)
net    = sold_price − COGS − fees − shipping_out
margin = net / sold_price
$/hour = net / (labour_minutes / 60)
```

`$/hour` is the number that tells you whether builds, send-in services, or lessons
are actually the best use of a Saturday. Nothing else answers that.

Bulk purchases amortise correctly because `avg_unit_cost` is derived from
`component_receipts` — a cheaper batch lowers the cost basis of every build after it,
automatically.

### Bottleneck detection

For every config: which component runs out first, and how many builds away that is.
Surface the top three constraints on the dashboard. With 60 shells and 3 donors,
"donor consoles: 3 builds remaining" is the headline, and that is the thing to go buy.

---

## 4. Screens

**Dashboard** — capital tied up (Σ component stock at cost + WIP), potential profit
across buildable stock, realised profit this month, awaiting shipment, builds by
stage, top 3 bottlenecks, low-stock components.

**Components** — table of everything held: on-hand, cost, reorder point, receipts
history. Add-a-receipt form.

**Configs** — sellable configurations with their BOM, base price, buildable count.

**Builds** — kanban by stage, drag to advance. Filter by stock vs send-in.

**Build detail** — options chosen, components consumed, cost breakdown, labour timer,
event timeline, margin.

**Shipments** — awaiting-packing queue, tracking entry.

**Reports** — margin by config, $/hour by work type, sell-through, days-to-sell,
monthly P&L, cost basis export for the accountant (CSV, no accounting integration).

---

## 5. Services and the configurator

The business is moving from store to service. Three revenue lines beyond stock builds:

1. **Send-in custom work** — customer posts their console, chooses shell and options.
   Same `builds` table, `kind = 'customer_send_in'`, no donor component consumed.
   This is the highest-margin line: no console cost, no capital tied up.
2. **DS modding** — same machinery, different `fits_models`. No schema change needed.
3. **Build guides** — free one as a lead magnet, paid one as a digital product. Sell
   via the existing `item_type` mechanism (add `'digital'`).

### Shell configurator

Let the customer build their console on the on-screen Game Boy and see it before
buying. The site already renders a shell image with positioned overlays, so the
foundation exists.

**Implementation:** layered PNGs, not CSS filters. The existing `hue-rotate` easter
egg is fine as a toy, but a purchase preview has to show the actual product — a
hue-rotated grey shell is a misrepresentation of what arrives in the box, and that is
a refund and a bad review. Photograph or render each real colorway once:

```
/static/shells/<model>/<colorway>.png     base shell
/static/shells/<model>/buttons-<color>.png
/static/shells/<model>/screen-ips.png     overlay when IPS selected
```

Stack them in the existing `.gameboy-container`. Selecting a colorway swaps the base
layer; ticking IPS swaps the screen layer and adds its price.

**Config persistence:** store the chosen combination on the order. Then a customer
who has bought can see *their* console rendered on the site — which is the natural
hook into the game later: you play on the shell you actually own.

---

## 6. Direct-to-Supabase: the security requirement

Going straight from a front-end to Supabase means **Row Level Security is the only
thing standing between the internet and this data** — cost basis, supplier prices,
margins, and customer addresses. The anon key is public by definition; it ships in
the JavaScript.

Non-negotiable:

- **RLS enabled on every ops table.** A table without a policy is readable by anyone
  who opens devtools.
- **Supabase Auth** with your account as the only user; policies check
  `auth.uid()`, not a shared password.
- **Never the service_role key in the front-end.** It bypasses RLS entirely. It stays
  server-side, in the storefront, only.
- Storefront-readable tables (`inventory`) keep a public read policy; ops tables get
  none.

Get this wrong and competitors can read your supplier costs and customers can read
each other's addresses.

---

## 7. Build order

1. `components` + `component_receipts` + the movements ledger, with an intake form.
   **Load the 60 shells here first** — that alone starts answering the cost question.
2. `configs` + BOM + `config_buildable` view.
3. `builds` + events + kanban.
4. Cost/margin rollups and the dashboard.
5. Shipments queue.
6. Storefront: point `inventory.stock` at `config_buildable`.
7. Shell configurator.
8. Reports.

Stages 1–2 replace the spreadsheet and answer "what do I own and what can I build".
Ship that before building any dashboards.

SKUs are enough for now; QR labels are worth revisiting past ~50 concurrent builds.
