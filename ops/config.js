// Supabase connection for the ops app.
//
// Fill these in and the setup screen never appears again - on any machine, any
// browser, and on ops.gameboyretreat.com once deployed.
//
// The anon key is PUBLIC by design. It is meant to ship in the browser, and it
// grants nothing on its own: every ops table has Row Level Security requiring a
// signed-in user, and `anon` is revoked on every view. Committing it is normal
// practice for a Supabase front-end.
//
// The service_role key must NEVER go here - it bypasses RLS entirely.
window.OPS_CONFIG = {
  url: '',   // https://tsfssgspkrwxcdjeyejp.supabase.co
  key: ''    // Supabase -> Settings -> API -> anon public
};
