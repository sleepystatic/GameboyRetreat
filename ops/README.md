# Operations app

Internal inventory and cost tracking. Static single-page app that talks to Supabase
directly — no server of its own.

## Running locally

```bash
cd ops
python -m http.server 5077
```

Open <http://127.0.0.1:5077>. Use a local server rather than opening the file
directly; `file://` gives a `null` origin that some APIs reject.

On first load it asks for the Supabase **project URL** and **anon** key
(Supabase → Settings → API). Both are stored in this browser's `localStorage`.

## Deploying to Vercel

1. Vercel → Add New → Project → import the `GameboyRetreat` repo.
2. **Root Directory: `ops`** — this is the important one. Without it Vercel tries
   to build the Flask app.
3. Framework Preset: **Other**. No build command, no output directory.
4. Deploy.
5. Settings → Domains → add `ops.gameboyretreat.com`, then add the CNAME Vercel
   gives you at your DNS host.

`vercel.json` sets `X-Robots-Tag: noindex` and clickjacking/MIME headers, and
`robots.txt` disallows crawlers, so the tool stays out of search results.

## Security model

The anon key ships in the browser. That is normal and expected — **Row Level
Security is what protects the data**, not the key being secret.

- Every ops table has RLS on with a policy granting access only to `authenticated`.
- Views use `security_invoker = true`, so RLS on the underlying tables still applies
  when reading through them.
- `anon` is explicitly revoked on every view.
- The `service_role` key must **never** appear in this app. It bypasses RLS.

Sanity check any time: sign out and reload. You should see no data. If you can read
components while signed out, a policy is wrong.

## Adding a user

There is no signup by design. Supabase → Authentication → Users → Add user, with
*Auto Confirm User* ticked.

## Schema

See `../db/001_operations.sql`. Plain DDL only — no functions, triggers or `DO`
blocks, because the Supabase SQL Editor splits scripts on semicolons and mis-parses
dollar-quoted bodies, silently aborting the rest of the script.
