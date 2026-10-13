from flask import Flask, render_template, request, jsonify, redirect, Response
from markupsafe import escape
from werkzeug.middleware.proxy_fix import ProxyFix
import stripe
import os
import re
import json
import hmac
import threading
import unicodedata
import uuid
from contextlib import contextmanager
from datetime import datetime
import requests
from dotenv import load_dotenv
import psycopg2
from psycopg2 import pool
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user

load_dotenv()

app = Flask(__name__)

# Render terminates TLS and forwards plain HTTP, so without this every
# request.url_root reads http:// - which leaked into canonical tags, the
# sitemap, and Stripe's return URLs. Trust exactly one proxy hop.
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

# The session signing key. This repository is public, so a hardcoded fallback
# would be a published key: anyone could sign a session cookie claiming to be
# the admin. With no SECRET_KEY set we generate a random one instead - sessions
# then reset on every restart and differ between workers, which makes logins
# flaky. That is deliberate: a visible annoyance rather than a silent hole.
_secret = os.getenv('SECRET_KEY')
if not _secret:
    import secrets as _secrets
    _secret = _secrets.token_hex(32)
    print("⚠️ SECRET_KEY is not set - using a random per-process key. "
          "Admin logins will not survive restarts. Set SECRET_KEY in the environment.")
app.secret_key = _secret

# Public origin used for canonical URLs, the sitemap, structured data and Stripe
# return URLs. Pinning it stops gameboyretreat.onrender.com (or any other host the
# app answers on) from being indexed as a duplicate of the real domain.
SITE_URL = (os.environ.get('SITE_URL') or '').rstrip('/')


def site_root():
    """The canonical origin with no trailing slash."""
    return SITE_URL or request.url_root.rstrip('/')


# Optional: Google Search Console HTML-tag verification.
GOOGLE_SITE_VERIFICATION = os.environ.get('GOOGLE_SITE_VERIFICATION', '')

# Flask-Login setup
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'admin_login'

# Simple user class (no database needed for single admin)
class User(UserMixin):
    def __init__(self, id):
        self.id = id

@login_manager.user_loader
def load_user(user_id):
    if user_id == '1':  # Only one admin user
        return User(user_id)
    return None

# Stripe configuration
stripe.api_key = os.getenv('STRIPE_SECRET_KEY')
STRIPE_PUBLISHABLE_KEY = os.getenv('STRIPE_PUBLISHABLE_KEY')

# Email - Resend (replaces Mailgun/Flask-Mail)
#
# Resend is a plain HTTPS API, so there is no SMTP connection to keep alive and no
# extra dependency beyond `requests`. MAIL_FROM must be an address on a domain you
# have verified in the Resend dashboard, or the send is rejected.
RESEND_API_KEY = os.environ.get('RESEND_API_KEY')
MAIL_RECIPIENT = os.environ.get('MAIL_RECIPIENT')
RESEND_ENDPOINT = 'https://api.resend.com/emails'

# Transactional mail (receipts, seller submissions) sends from the root domain.
# Marketing sends from a subdomain so that if a newsletter ever collects spam
# complaints, the damaged reputation is confined there and order confirmations
# keep landing in inboxes.
MAIL_FROM = os.environ.get('MAIL_FROM', 'Gameboy Retreat <orders@gameboyretreat.com>')
MARKETING_FROM = os.environ.get(
    'MARKETING_FROM', 'Gameboy Retreat <hello@news.gameboyretreat.com>'
)

# Resend Audience that newsletter signups are added to.
RESEND_AUDIENCE_ID = os.environ.get('RESEND_AUDIENCE_ID')


def send_email(subject, body, to=None, reply_to=None):
    """Send a plain-text email through Resend.

    Returns True on success. Never raises - callers treat mail as best-effort so a
    mail outage cannot take down a form submission that already hit the database.
    """
    recipient = to or MAIL_RECIPIENT
    if not RESEND_API_KEY or not recipient:
        print("⚠️ Email skipped: RESEND_API_KEY or MAIL_RECIPIENT is not set")
        return False

    payload = {
        'from': MAIL_FROM,
        'to': [recipient] if isinstance(recipient, str) else list(recipient),
        'subject': subject,
        'text': body,
    }
    if reply_to:
        payload['reply_to'] = reply_to

    try:
        response = requests.post(
            RESEND_ENDPOINT,
            headers={
                'Authorization': f'Bearer {RESEND_API_KEY}',
                'Content-Type': 'application/json',
            },
            json=payload,
            timeout=10,
        )
        if response.status_code >= 400:
            print(f"⚠️ Resend rejected the message ({response.status_code}): {response.text}")
            return False
        print(f"✅ Email sent via Resend: {response.json().get('id', 'no id')}")
        return True
    except requests.RequestException as e:
        print(f"⚠️ Resend request failed: {e}")
        return False


EMAIL_RE = re.compile(r'^[^@\s]+@[^@\s]+\.[a-zA-Z]{2,}$')


def add_to_audience(email, source='site'):
    """Add a subscriber to the Resend audience.

    Returns (ok, message). The local subscribers table is the source of truth -
    if Resend is down or the audience id is unset, the address is still captured
    and can be synced later, so a signup is never silently lost.
    """
    email = (email or '').strip().lower()
    if not EMAIL_RE.match(email):
        return False, 'That does not look like a valid email address.'

    # Always record locally first.
    try:
        with get_cursor(commit=True) as cursor:
            cursor.execute('''
                INSERT INTO subscribers (email, source)
                VALUES (%s, %s)
                ON CONFLICT (email) DO UPDATE SET unsubscribed_at = NULL
            ''', (email, source))
    except Exception as e:
        print(f"⚠️ Could not store subscriber {email}: {e}")
        return False, 'Something went wrong. Please try again.'

    if not (RESEND_API_KEY and RESEND_AUDIENCE_ID):
        print(f"ℹ️ Stored {email} locally; Resend audience not configured")
        return True, "You're on the list!"

    try:
        response = requests.post(
            f'https://api.resend.com/audiences/{RESEND_AUDIENCE_ID}/contacts',
            headers={
                'Authorization': f'Bearer {RESEND_API_KEY}',
                'Content-Type': 'application/json',
            },
            json={'email': email, 'unsubscribed': False},
            timeout=10,
        )
        if response.status_code >= 400:
            # Already-subscribed is a success from the visitor's point of view.
            print(f"⚠️ Resend audience rejected {email} "
                  f"({response.status_code}): {response.text}")
        else:
            with get_cursor(commit=True) as cursor:
                cursor.execute(
                    "UPDATE subscribers SET synced_at = NOW() WHERE email = %s",
                    (email,),
                )
    except requests.RequestException as e:
        print(f"⚠️ Could not sync {email} to Resend: {e}")

    return True, "You're on the list!"

# PostgreSQL Database connection pool
#
# The pool is built lazily and rebuilt on demand. Supabase closes every server-side
# connection when the project sleeps, and psycopg2 never notices - it keeps handing
# out sockets that are already dead. Checking the connection before use and dropping
# the whole pool when one turns out to be stale is what lets the app recover on its
# own once the database wakes back up.
DATABASE_URL = os.environ.get('DATABASE_URL')

# Each gunicorn worker builds its own pool, so the real connection ceiling is
# workers x DB_POOL_MAX. Keep that product comfortably under the Supabase
# pooler's client limit or new connections start getting refused under load.
DB_POOL_MIN = int(os.environ.get('DB_POOL_MIN', '1'))
DB_POOL_MAX = int(os.environ.get('DB_POOL_MAX', '8'))

db_pool = None
_pool_lock = threading.Lock()


def _ensure_pool():
    """Return the pool, creating it on first use or after a reset."""
    global db_pool
    if db_pool is None:
        with _pool_lock:
            if db_pool is None:
                db_pool = psycopg2.pool.SimpleConnectionPool(
                    DB_POOL_MIN, DB_POOL_MAX, DATABASE_URL
                )
                print("✅ Database connection pool created successfully")
    return db_pool


def _reset_pool():
    """Throw away the pool so the next request builds a fresh one."""
    global db_pool
    with _pool_lock:
        if db_pool is not None:
            try:
                db_pool.closeall()
            except Exception:
                pass
            db_pool = None


def _get_live_conn():
    """Check out a connection that is actually alive, rebuilding the pool if needed."""
    last_err = None
    for attempt in range(2):
        try:
            pool_ = _ensure_pool()
        except (psycopg2.OperationalError, psycopg2.InterfaceError):
            # A brand new pool that cannot connect means the database is unreachable
            # (wrong host, no network, project paused). Nothing is stale, so retrying
            # only doubles the wait and the log noise.
            _reset_pool()
            raise

        conn = None
        try:
            conn = pool_.getconn()
            with conn.cursor() as probe:
                probe.execute('SELECT 1')
            conn.rollback()
            return pool_, conn
        except (psycopg2.OperationalError, psycopg2.InterfaceError, psycopg2.pool.PoolError) as e:
            last_err = e
            if conn is not None:
                try:
                    pool_.putconn(conn, close=True)
                except Exception:
                    pass
            _reset_pool()
            if attempt == 0:
                print(f"⚠️ Stale database connection ({e}); rebuilding pool")
    raise last_err


@contextmanager
def get_cursor(commit=False):
    """Hand out a cursor and always return its connection to the pool."""
    pool_, conn = _get_live_conn()
    cursor = conn.cursor()
    try:
        yield cursor
        if commit:
            conn.commit()
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        raise
    finally:
        try:
            cursor.close()
        except Exception:
            pass
        try:
            pool_.putconn(conn)
        except Exception:
            pass


@app.route('/admin')
def admin_login():
    """Show login page"""
    if current_user.is_authenticated:
        return redirect('/admin/dashboard')
    return render_template('admin_login.html')


# ------------------------------------------------------------
# Login throttling
#
# The admin is a single shared password with no username, so it is exactly the
# shape of thing that gets brute-forced. Five failures per IP in fifteen minutes,
# then a lockout. In-memory and per-worker, so the effective limit is a few times
# higher across gunicorn workers - still enough to make guessing impractical.
# ------------------------------------------------------------

LOGIN_MAX_FAILURES = 5
LOGIN_WINDOW_SECONDS = 15 * 60
_login_failures = {}
_login_lock = threading.Lock()


def _client_ip():
    # ProxyFix has already resolved X-Forwarded-For into remote_addr.
    return request.remote_addr or 'unknown'


def _login_blocked(ip):
    now = datetime.now().timestamp()
    with _login_lock:
        recent = [t for t in _login_failures.get(ip, []) if now - t < LOGIN_WINDOW_SECONDS]
        _login_failures[ip] = recent
        return len(recent) >= LOGIN_MAX_FAILURES


def _record_login_failure(ip):
    with _login_lock:
        _login_failures.setdefault(ip, []).append(datetime.now().timestamp())


@app.route('/admin/login', methods=['GET', 'POST'])
def admin_login_post():
    """Handle login. GET just sends you to the login page - the form posts here,
    but people (and browsers restoring tabs) hit the URL directly too."""
    if request.method == 'GET':
        return redirect('/admin')

    ip = _client_ip()
    if _login_blocked(ip):
        print(f"⚠️ Admin login locked out for {ip}")
        return render_template(
            'admin_login.html',
            error='Too many failed attempts. Try again in 15 minutes.'
        ), 429

    password = request.form.get('password') or ''
    expected = os.getenv('ADMIN_PASSWORD') or ''

    if not (expected and hmac.compare_digest(password, expected)):
        _record_login_failure(ip)

    if expected and hmac.compare_digest(password, expected):
        with _login_lock:
            _login_failures.pop(ip, None)
        user = User('1')
        login_user(user, remember=True)
        return redirect('/admin/dashboard')
    else:
        return render_template('admin_login.html', error='Invalid password')


@app.route('/admin/dashboard')
@login_required
def admin_dashboard():
    """Admin dashboard - view orders"""
    try:
        with get_cursor() as cursor:
            cursor.execute('SELECT * FROM orders ORDER BY created_at DESC LIMIT 50')
            orders = cursor.fetchall()

        return render_template('admin_dashboard.html', orders=orders)
    except Exception as e:
        return f"Error: {e}", 500


@app.route('/admin/logout')
@login_required
def admin_logout():
    logout_user()
    return redirect('/admin')


@app.route('/admin/listings')
@login_required
def admin_listings():
    """Inventory overview with links to edit each row."""
    try:
        items = fetch_listings()
    except Exception as e:
        return f"Error loading inventory: {e}", 500
    return render_template(
        'admin_listings.html',
        items=items,
        categories=category_labels(),
        statuses=LISTING_STATUSES,
        storage_ok=storage_configured(),
    )


@app.route('/admin/listings/new', methods=['GET', 'POST'])
@login_required
def admin_listing_new():
    """Create a listing, uploading any attached photos to Supabase Storage."""
    categories = category_labels()
    if request.method == 'GET':
        return render_template(
            'admin_listing_form.html',
            categories=categories,
            item_types=ITEM_TYPES,
            storage_ok=storage_configured(),
            item=None,
            photos=[],
        )

    title = (request.form.get('title') or '').strip()
    category = (request.form.get('category') or '').strip()
    item_type = (request.form.get('item_type') or 'console').strip()
    if item_type not in ITEM_TYPES:
        item_type = 'console'
    description = clean_description(request.form.get('description'))
    price_raw = (request.form.get('price') or '').strip()
    stock_raw = (request.form.get('stock') or '0').strip()

    errors = []
    if not title:
        errors.append('Title is required.')
    if not category:
        errors.append('Category is required.')
    elif category not in categories:
        errors.append('That category no longer exists - choose another.')
    try:
        price = float(price_raw)
        if price < 0:
            errors.append('Price cannot be negative.')
    except ValueError:
        price = 0.0
        errors.append('Price must be a number.')
    try:
        stock = int(stock_raw)
        if stock < 0:
            errors.append('Stock cannot be negative.')
    except ValueError:
        stock = 0
        errors.append('Stock must be a whole number.')

    if errors:
        return render_template(
            'admin_listing_form.html',
            categories=categories,
            item_types=ITEM_TYPES,
            storage_ok=storage_configured(),
            errors=errors,
            form=request.form,
            item=None,
            photos=[],
        ), 400

    # Unique slug
    try:
        with get_cursor() as cursor:
            cursor.execute("SELECT slug FROM inventory WHERE slug IS NOT NULL")
            taken = {r[0] for r in cursor.fetchall()}
    except Exception as e:
        return f"Database error: {e}", 500

    base = slugify(title)
    slug, n = base, 2
    while slug in taken:
        slug = f"{base}-{n}"
        n += 1

    # Photos in the order the editor set, then any pasted URLs. A failed upload
    # blocks the save here too - creating the listing without the photo used
    # to happen silently.
    images, photo_errors = assemble_photos([], slug)
    if photo_errors:
        return render_template(
            'admin_listing_form.html',
            categories=categories,
            item_types=ITEM_TYPES,
            storage_ok=storage_configured(),
            errors=photo_errors,
            form=request.form,
            item=None,
            photos=[],
        ), 400

    for line in (request.form.get('image_urls') or '').splitlines():
        line = line.strip()
        if line:
            images.append(line)

    try:
        with get_cursor(commit=True) as cursor:
            cursor.execute('''
                INSERT INTO inventory (category, title, description, price, stock,
                                       images, slug, item_type)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id
            ''', (category, title, description, price, stock, json.dumps(images),
                  slug, item_type))
            new_id = cursor.fetchone()[0]
    except Exception as e:
        return f"Could not save listing: {e}", 500

    print(f"✅ Created listing #{new_id} ({slug}) with {len(images)} image(s)")
    return redirect('/admin/listings')


LISTING_STATUSES = {
    'active': 'For sale',
    'coming_soon': 'Coming soon (visible, not buyable)',
    'hidden': 'Hidden (not shown anywhere)',
}


@app.route('/admin/analytics')
@login_required
def admin_analytics():
    """Where visitors came from, what they looked at, and what they bought."""
    try:
        days = int(request.args.get('days', 30))
    except ValueError:
        days = 30
    days = days if days in (7, 30, 90, 180) else 30

    def q(sql, params=()):
        with get_cursor() as cursor:
            cursor.execute(sql, params)
            return cursor.fetchall()

    window = "created_at > NOW() - make_interval(days => %s)"

    try:
        visitors = q(f"""SELECT COUNT(DISTINCT session_id) FROM events
                         WHERE type='pageview' AND {window}""", (days,))[0][0]
        pageviews = q(f"SELECT COUNT(*) FROM events WHERE type='pageview' AND {window}",
                      (days,))[0][0]
        order_rows = q(f"""SELECT COUNT(*), COALESCE(SUM(amount), 0) FROM orders
                           WHERE {window}""", (days,))[0]

        # --- by source: visitors from events, orders and revenue from orders ---
        source_visits = dict(q(f"""
            SELECT COALESCE(ref, 'direct'), COUNT(DISTINCT session_id)
            FROM events WHERE type='pageview' AND {window} GROUP BY 1""", (days,)))
        source_orders = {r[0]: (r[1], float(r[2])) for r in q(f"""
            SELECT COALESCE(ref, 'direct'), COUNT(*), COALESCE(SUM(amount), 0)
            FROM orders WHERE {window} GROUP BY 1""", (days,))}

        sources = []
        for name in set(source_visits) | set(source_orders):
            v = source_visits.get(name, 0)
            o, rev = source_orders.get(name, (0, 0.0))
            sources.append({
                'name': name, 'visitors': v, 'orders': o, 'revenue': rev,
                'conversion': (o / v * 100) if v else None,
            })
        sources.sort(key=lambda s: (-s['visitors'], -s['orders']))

        pages = q(f"""SELECT path, COUNT(*), COUNT(DISTINCT session_id) FROM events
                      WHERE type='pageview' AND {window} AND path IS NOT NULL
                      GROUP BY path ORDER BY 2 DESC LIMIT 15""", (days,))

        # Listing interest from both surfaces: in-console views carry the slug,
        # web views are pageviews of /listing/<slug>.
        listing_counts = {}
        for slug, n in q(f"""SELECT listing, COUNT(*) FROM events
                             WHERE type='listing_view' AND listing IS NOT NULL AND {window}
                             GROUP BY listing""", (days,)):
            listing_counts[slug] = listing_counts.get(slug, 0) + n
        for path, n in q(f"""SELECT path, COUNT(*) FROM events
                             WHERE type='pageview' AND path LIKE '/listing/%%' AND {window}
                             GROUP BY path""", (days,)):
            slug = path.split('/listing/', 1)[1].split('?')[0]
            listing_counts[slug] = listing_counts.get(slug, 0) + n

        titles = {i['slug']: i['title'] for i in fetch_listings(include_hidden=True)}
        top_listings = sorted(
            ({'slug': s, 'title': titles.get(s, s), 'views': n}
             for s, n in listing_counts.items()),
            key=lambda r: -r['views'])[:15]

        ctas = q(f"""SELECT detail, COUNT(*) FROM events
                     WHERE type='cta' AND {window} GROUP BY detail ORDER BY 2 DESC""", (days,))
        categories = q(f"""SELECT detail, COUNT(*) FROM events
                           WHERE type='category_view' AND {window}
                           GROUP BY detail ORDER BY 2 DESC""", (days,))

        def sessions_with(where):
            return q(f"""SELECT COUNT(DISTINCT session_id) FROM events
                         WHERE {where} AND {window}""", (days,))[0][0]

        funnel = [
            ('Visited', visitors),
            ('Viewed a listing', sessions_with(
                "(type='listing_view' OR (type='pageview' AND path LIKE '/listing/%%'))")),
            ('Added to cart', sessions_with("type='add_to_cart'")),
            ('Started checkout', sessions_with("type='checkout_start'")),
            ('Bought', order_rows[0]),
        ]

        daily = q(f"""SELECT date_trunc('day', created_at)::date AS d,
                             COUNT(DISTINCT session_id)
                      FROM events WHERE type='pageview' AND {window}
                      GROUP BY d ORDER BY d""", (days,))

        surfaces = q(f"""SELECT COALESCE(surface, 'unknown'), COUNT(*),
                                COALESCE(SUM(amount), 0)
                         FROM orders WHERE {window} GROUP BY 1 ORDER BY 2 DESC""", (days,))
    except Exception as e:
        return f"Analytics query failed: {e}", 500

    peak = max([n for _, n in daily] + [1])

    return render_template(
        'admin_analytics.html',
        days=days,
        visitors=visitors, pageviews=pageviews,
        order_count=order_rows[0], revenue=float(order_rows[1]),
        sources=sources, pages=pages, top_listings=top_listings,
        ctas=ctas, categories=categories, funnel=funnel,
        daily=daily, peak=peak, surfaces=surfaces,
        category_labels=category_labels(),
    )


@app.route('/admin/listings/<int:listing_id>/edit', methods=['GET', 'POST'])
@login_required
def admin_listing_edit(listing_id):
    """Edit an existing listing, including replacing its photos."""
    try:
        item = next((i for i in fetch_listings(include_hidden=True)
                     if i['id'] == listing_id), None)
    except Exception as e:
        return f"Database error: {e}", 500
    if not item:
        return "Listing not found", 404

    categories = category_labels()
    if request.method == 'GET':
        return render_template(
            'admin_listing_form.html',
            categories=categories,
            item_types=ITEM_TYPES,
            statuses=LISTING_STATUSES,
            storage_ok=storage_configured(),
            item=item,
            photos=photo_tiles(item['images_raw']),
        )

    title = (request.form.get('title') or '').strip()
    category = (request.form.get('category') or '').strip()
    description = clean_description(request.form.get('description'))
    item_type = (request.form.get('item_type') or 'console').strip()
    status = (request.form.get('status') or 'active').strip()
    if item_type not in ITEM_TYPES:
        item_type = 'console'
    if status not in LISTING_STATUSES:
        status = 'active'

    errors = []
    if not title:
        errors.append('Title is required.')
    if not category:
        errors.append('Category is required.')
    elif category not in categories and category != item['category']:
        # Keeping a listing's current category is always allowed, even one
        # missing from the table - the form offers it so a save never moves it.
        errors.append('That category no longer exists - choose another.')
    try:
        price = float(request.form.get('price') or 0)
    except ValueError:
        price, _ = 0.0, errors.append('Price must be a number.')
    try:
        stock = int(request.form.get('stock') or 0)
    except ValueError:
        stock, _ = 0, errors.append('Stock must be a whole number.')

    if errors:
        return render_template(
            'admin_listing_form.html',
            categories=categories, item_types=ITEM_TYPES,
            statuses=LISTING_STATUSES, storage_ok=storage_configured(),
            errors=errors, item=item, form=request.form,
            photos=photo_tiles(item['images_raw'], request.form),
        ), 400

    # Photos in the order the editor set, minus any left unticked, then pasted
    # URLs. Report upload problems instead of saving quietly without the photo -
    # a silent success that loses the image is the worst possible outcome here.
    images, photo_errors = assemble_photos(item['images_raw'], item['slug'])
    if photo_errors:
        return render_template(
            'admin_listing_form.html',
            categories=categories, item_types=ITEM_TYPES,
            statuses=LISTING_STATUSES, storage_ok=storage_configured(),
            errors=photo_errors, item=item, form=request.form,
            photos=photo_tiles(item['images_raw'], request.form),
        ), 400

    for line in (request.form.get('image_urls') or '').splitlines():
        line = line.strip()
        if line:
            images.append(line)

    try:
        with get_cursor(commit=True) as cursor:
            cursor.execute('''
                UPDATE inventory
                SET category = %s, title = %s, description = %s, price = %s,
                    stock = %s, images = %s, item_type = %s, status = %s,
                    updated_at = NOW()
                WHERE id = %s
            ''', (category, title, description, price, stock,
                  json.dumps(images), item_type, status, listing_id))
    except Exception as e:
        return f"Could not save listing: {e}", 500

    print(f"✅ Updated listing #{listing_id} ({item['slug']}), {len(images)} image(s)")
    return redirect('/admin/listings')


def storage_path_from_url(url):
    """The object path inside our bucket, or None if the URL is not ours."""
    marker = f"/storage/v1/object/public/{SUPABASE_BUCKET}/"
    if not url or not SUPABASE_URL or not url.startswith(SUPABASE_URL) or marker not in url:
        return None
    return url.split(marker, 1)[1]


def delete_storage_objects(urls):
    """Best-effort removal of uploaded photos. Returns how many were removed.

    Only touches objects in this project's listings bucket - any other URL (an
    old static path, an external link) is ignored rather than guessed at.
    """
    paths = [p for p in (storage_path_from_url(u) for u in urls) if p]
    if not paths or not storage_configured():
        return 0
    try:
        response = requests.delete(
            f"{SUPABASE_URL}/storage/v1/object/{SUPABASE_BUCKET}",
            headers={
                'Authorization': f'Bearer {SUPABASE_SERVICE_KEY}',
                'Content-Type': 'application/json',
            },
            json={'prefixes': paths},
            timeout=20,
        )
        if response.status_code >= 400:
            print(f"⚠️ Storage cleanup failed ({response.status_code}): {response.text[:200]}")
            return 0
        return len(response.json()) if response.text else len(paths)
    except requests.RequestException as e:
        print(f"⚠️ Storage cleanup failed: {e}")
        return 0


@app.route('/admin/listings/<int:listing_id>/delete', methods=['POST'])
@login_required
def admin_listing_delete(listing_id):
    """Permanently remove a listing and its uploaded photos.

    Past orders are unaffected: they store their own snapshot of what was bought
    rather than a reference to this row.
    """
    try:
        with get_cursor() as cursor:
            cursor.execute(
                "SELECT title, slug, images FROM inventory WHERE id = %s", (listing_id,)
            )
            row = cursor.fetchone()
            if not row:
                return redirect('/admin/listings')
            title, slug, images = row

            # Refuse while someone is mid-checkout. Deleting would cascade away
            # their hold and leave the payment with nothing to decrement.
            cursor.execute('''
                SELECT COALESCE(SUM(quantity), 0) FROM reservations
                WHERE listing_id = %s AND status = 'held' AND expires_at > NOW()
            ''', (listing_id,))
            if cursor.fetchone()[0] > 0:
                return (f"'{title}' is in someone's checkout right now. "
                        f"Hide it instead, or try again once the hold expires."), 409

            # Do not delete a photo another listing still points at.
            if isinstance(images, str):
                images = json.loads(images or '[]')
            images = images or []
            cursor.execute(
                "SELECT images FROM inventory WHERE id <> %s AND images IS NOT NULL",
                (listing_id,),
            )
            in_use = set()
            for (other,) in cursor.fetchall():
                if isinstance(other, str):
                    other = json.loads(other or '[]')
                in_use.update(other or [])
    except Exception as e:
        return f"Database error: {e}", 500

    removable = [u for u in images if u not in in_use]

    try:
        with get_cursor(commit=True) as cursor:
            cursor.execute("DELETE FROM inventory WHERE id = %s", (listing_id,))
    except Exception as e:
        return f"Could not delete listing: {e}", 500

    removed = delete_storage_objects(removable)
    print(f"🗑️ Deleted listing #{listing_id} '{title}' ({slug}); "
          f"removed {removed} stored photo(s)")
    return redirect('/admin/listings')


@app.route('/admin/listings/<int:listing_id>/stock', methods=['POST'])
@login_required
def admin_update_stock(listing_id):
    """Quick stock and availability adjustment from the inventory table."""
    try:
        stock = max(0, int(request.form.get('stock', '0')))
    except ValueError:
        return redirect('/admin/listings')

    status = request.form.get('status', 'active')
    if status not in LISTING_STATUSES:
        status = 'active'

    try:
        with get_cursor(commit=True) as cursor:
            cursor.execute(
                "UPDATE inventory SET stock = %s, status = %s WHERE id = %s",
                (stock, status, listing_id),
            )
    except Exception as e:
        return f"Could not update listing: {e}", 500

    return redirect('/admin/listings')

# ============================================================
# SUPABASE STORAGE
# Render's filesystem is ephemeral - anything written into static/ is gone on the
# next deploy. Listing photos therefore go to Supabase Storage, resized and
# converted to WebP on the way in so a 5MB phone photo lands at roughly 150KB.
# ============================================================

SUPABASE_URL = (os.environ.get('SUPABASE_URL') or '').rstrip('/')
SUPABASE_SERVICE_KEY = os.environ.get('SUPABASE_SERVICE_KEY')
SUPABASE_BUCKET = os.environ.get('SUPABASE_BUCKET', 'listings')

MAX_IMAGE_EDGE = 1600      # px on the longest side
WEBP_QUALITY = 82

# iPhones shoot HEIC by default, so most photos arriving here are HEIC unless
# the phone has been told otherwise. Pillow cannot read it without this plugin,
# and renaming the file does not help - the format is detected from the bytes.
try:
    from pillow_heif import register_heif_opener
    register_heif_opener()
    HEIC_SUPPORTED = True
except ImportError:
    HEIC_SUPPORTED = False
    print("⚠️ pillow-heif not installed; HEIC/HEIF photos will be rejected")

ALLOWED_UPLOAD_TYPES = {
    'image/jpeg', 'image/jpg', 'image/png', 'image/webp', 'image/gif',
    'image/heic', 'image/heif',
    # Some browsers send an empty or generic type for HEIC. The real format
    # check happens when Pillow opens the bytes, so this is only a first pass.
    'application/octet-stream', '',
}


def storage_configured():
    return bool(SUPABASE_URL and SUPABASE_SERVICE_KEY)


def process_image(file_storage, max_edge=MAX_IMAGE_EDGE, quality=WEBP_QUALITY):
    """Downscale to max_edge and re-encode as WebP. Returns bytes."""
    from PIL import Image, ImageOps
    import io

    image = Image.open(file_storage.stream)
    image = ImageOps.exif_transpose(image)      # honour phone orientation
    if image.mode not in ('RGB', 'RGBA'):
        image = image.convert('RGB')

    image.thumbnail((max_edge, max_edge), Image.LANCZOS)

    buffer = io.BytesIO()
    image.save(buffer, format='WEBP', quality=quality, method=6)
    return buffer.getvalue()


PREVIEW_EDGE = 480         # px; plenty for a tile in the photo editor


@app.route('/admin/photo-preview', methods=['POST'])
@login_required
def admin_photo_preview():
    """A small WebP of one picked photo, for the photo editor's tiles.

    Only Safari can display HEIC - the format iPhones shoot - so in Chrome or
    Edge a new HEIC photo's tile stayed blank and could not be told apart from
    the others. This runs the same decoder as the real upload (pillow-heif plus
    the EXIF rotation fix), so the preview also matches what will be saved.
    Nothing is stored.
    """
    photo = request.files.get('photo')
    if not photo or not photo.filename:
        return 'No photo sent', 400
    try:
        data = process_image(photo, max_edge=PREVIEW_EDGE, quality=70)
    except Exception as e:
        print(f"⚠️ Preview failed for {photo.filename}: {e}")
        return 'This file is not an image the server can read', 422
    return Response(data, mimetype='image/webp', headers={'Cache-Control': 'no-store'})


def upload_listing_image(file_storage, slug, index):
    """Upload one processed image. Returns its public URL, or None on failure."""
    if not storage_configured():
        print("⚠️ Upload skipped: SUPABASE_URL / SUPABASE_SERVICE_KEY not set")
        return None

    try:
        data = process_image(file_storage)
    except Exception as e:
        name = getattr(file_storage, 'filename', 'image')
        if 'cannot identify image file' in str(e):
            hint = (' - the file is not a format this server can read'
                    + ('' if HEIC_SUPPORTED else '; HEIC support is not installed'))
        else:
            hint = ''
        print(f"⚠️ Could not process {name}: {e}{hint}")
        return None

    # Timestamped name so re-uploading never collides with an existing object.
    name = f"{slug}/{int(datetime.now().timestamp())}-{index}.webp"
    url = f"{SUPABASE_URL}/storage/v1/object/{SUPABASE_BUCKET}/{name}"

    try:
        response = requests.post(
            url,
            headers={
                'Authorization': f'Bearer {SUPABASE_SERVICE_KEY}',
                'Content-Type': 'image/webp',
                'x-upsert': 'true',
            },
            data=data,
            timeout=30,
        )
        if response.status_code >= 400:
            print(f"⚠️ Storage upload failed ({response.status_code}): {response.text}")
            return None
    except requests.RequestException as e:
        print(f"⚠️ Storage upload failed: {e}")
        return None

    return f"{SUPABASE_URL}/storage/v1/object/public/{SUPABASE_BUCKET}/{name}"


# Photo editor token for "the nth file picked in this request's upload field".
NEW_PHOTO = 'new:'


def photo_tiles(existing, form=None):
    """The photo editor's tiles, [{'src', 'kept'}], in display order.

    Normally the stored order with everything kept. After a failed save, the
    order and ticks from the submitted form, so an error does not throw away
    the rearranging. Unsaved uploads cannot be carried over - browsers never
    refill a file input.
    """
    order = form.getlist('photo_order') if form else []
    if not order:
        return [{'src': p, 'kept': True} for p in existing]

    keep = set(form.getlist('keep_images'))
    known = set(existing)
    tiles, seen = [], set()
    for token in order:
        if token in known and token not in seen:
            seen.add(token)
            tiles.append({'src': token, 'kept': token in keep})
    tiles += [{'src': p, 'kept': True} for p in existing if p not in seen]
    return tiles


def assemble_photos(existing, slug):
    """A listing's photo URLs after a save, in the order the editor set.

    The form posts `photo_order`: every tile the editor showed, in page order -
    a stored photo's URL, or 'new:<n>' for the nth file in the upload field.
    `keep_images` is the ticked subset. Anything the editor never showed is
    kept and goes last: a photo added from another tab since this form loaded,
    or every upload when JavaScript is off. So a photo only ever disappears
    because its box was unticked.

    Returns (urls, errors). On any error the caller must not save, and photos
    this request already uploaded are deleted again rather than orphaned.
    """
    # A form without photo_order predates reordering: stored order, ticks only.
    order = request.form.getlist('photo_order') or list(existing)
    keep = set(request.form.getlist('keep_images'))
    uploads = request.files.getlist('photos')
    picked = {n for n, f in enumerate(uploads) if f and f.filename}
    known = set(existing)

    if picked and not storage_configured():
        return [], ['Photo uploads are not configured on this server '
                    '(SUPABASE_URL / SUPABASE_SERVICE_KEY). Restart the app '
                    'after setting them.']

    images, errors, uploaded, seen = [], [], [], set()

    def upload(n):
        f = uploads[n]
        if f.mimetype not in ALLOWED_UPLOAD_TYPES:
            errors.append(f'{f.filename}: unsupported type {f.mimetype}')
            return
        url = upload_listing_image(f, slug, n)
        if url:
            uploaded.append(url)
            images.append(url)
        else:
            errors.append(f'{f.filename}: upload failed, see server log')

    for token in order:
        if token in seen:
            continue
        seen.add(token)
        if token.startswith(NEW_PHOTO):
            try:
                n = int(token[len(NEW_PHOTO):])
            except ValueError:
                continue
            if n in picked and token in keep:
                upload(n)
        elif token in known and token in keep:
            # Only this listing's own photos - the form cannot smuggle in a URL.
            images.append(token)

    images += [p for p in existing if p not in seen]
    for n in sorted(picked):
        if f'{NEW_PHOTO}{n}' not in seen:
            upload(n)

    if errors:
        delete_storage_objects(uploaded)
        return [], errors
    return images, []


def slugify(value):
    """Turn a title into a URL-safe slug: 'Rayquaza Edition SP' -> 'rayquaza-edition-sp'."""
    value = unicodedata.normalize('NFKD', str(value))
    value = value.encode('ascii', 'ignore').decode('ascii')
    value = re.sub(r'[^\w\s-]', '', value).strip().lower()
    value = re.sub(r'[-\s]+', '-', value)
    return value or 'item'


def clean_description(text):
    """Tidy a listing description without flattening it.

    Line breaks and indents are the formatting - the listing page and the Game
    Boy show them as typed - so only blank lines at either end and trailing
    spaces go. A plain .strip() would also eat the first line's indent.
    """
    text = (text or '').replace('\r\n', '\n').replace('\r', '\n')
    lines = [line.rstrip() for line in text.split('\n')]
    while lines and not lines[0]:
        lines.pop(0)
    return '\n'.join(lines).rstrip('\n')


def backfill_slugs():
    """Give every listing a unique slug. Safe to run repeatedly."""
    try:
        with get_cursor(commit=True) as cursor:
            cursor.execute("SELECT id, title FROM inventory WHERE slug IS NULL OR slug = ''")
            rows = cursor.fetchall()
            if not rows:
                return

            cursor.execute("SELECT slug FROM inventory WHERE slug IS NOT NULL AND slug <> ''")
            taken = {r[0] for r in cursor.fetchall()}

            for item_id, title in rows:
                base = slugify(title)
                slug, n = base, 2
                while slug in taken:
                    slug = f"{base}-{n}"
                    n += 1
                taken.add(slug)
                cursor.execute("UPDATE inventory SET slug = %s WHERE id = %s", (slug, item_id))
            print(f"✅ Backfilled {len(rows)} listing slug(s)")
    except Exception as e:
        print(f"⚠️ Slug backfill skipped: {e}")


APP_DIR = os.path.dirname(os.path.abspath(__file__))


def normalize_image(path):
    """Stored paths look like 'static/images/x.png'. Make them root-relative."""
    if not path:
        return ''
    path = str(path)
    if path.startswith(('http://', 'https://', '/')):
        return path
    return '/' + path.lstrip('/')


def image_exists(path):
    """False for a local /static/ path whose file is no longer on disk.

    Remote URLs are assumed fine - checking them would mean a network call per
    image per page view. Local files are cheap to check, and they are the ones
    that go missing when the static/images folder gets cleared out.
    """
    if not path or not path.startswith('/static/'):
        return bool(path)
    return os.path.isfile(os.path.join(APP_DIR, path.lstrip('/').replace('/', os.sep)))


def fetch_listings(include_hidden=False):
    """Every listing as a list of dicts. Shared by the API and the public pages."""
    # `available` subtracts stock that is currently held by an in-flight checkout,
    # so a unit someone is paying for right now stops showing as buyable.
    where = 'WHERE i.status <> %s' if include_hidden is False else 'WHERE TRUE'
    params = ('hidden',) if include_hidden is False else ()

    with get_cursor() as cursor:
        cursor.execute(f'''
            SELECT i.id, i.category, i.title, i.stock, i.price, i.description,
                   i.images, i.slug, i.status, i.item_type,
                   COALESCE((
                       SELECT SUM(r.quantity) FROM reservations r
                       WHERE r.listing_id = i.id
                         AND r.status = 'held'
                         AND r.expires_at > NOW()
                   ), 0) AS held
            FROM inventory i
            {where}
            ORDER BY i.category, i.title
        ''', params)
        rows = cursor.fetchall()

    items = []
    for (item_id, category, title, stock, price, description,
         images, slug, status, item_type, held) in rows:
        if isinstance(images, str):
            try:
                images_list = json.loads(images)
            except ValueError:
                images_list = []
        elif isinstance(images, list):
            images_list = images
        else:
            images_list = []

        # Labour has no inventory: a repair slot cannot sell out the way a console
        # can, so services and lessons ignore stock entirely.
        is_service = item_type in SERVICE_TYPES

        if is_service:
            available = 1
            purchasable = status == 'active'
        else:
            available = max(0, int(stock) - int(held))
            purchasable = available > 0 and status == 'active'

        # Older rows were saved with the browser's \r\n line endings.
        description = clean_description(description)

        items.append({
            'id': item_id,
            'category': category,
            'title': title,
            'stock': stock,
            'held': int(held),
            'available': available,
            'status': status,
            'item_type': item_type,
            'is_service': is_service,
            'price': float(price),
            'description': description,
            # One line, for meta tags and card excerpts: places where line
            # breaks would be collapsed anyway and indents would eat the length.
            'summary': ' '.join(description.split()),
            # A dead local path renders as a broken image on the live site;
            # dropping it lets the "No photo" placeholder show instead.
            'images': [p for p in (normalize_image(x) for x in images_list)
                       if image_exists(p)],
            # Every stored path including missing ones, so the admin editor can
            # still show (and let you remove) a dead reference.
            'images_raw': [normalize_image(x) for x in images_list],
            'slug': slug or slugify(title),
            # in_stock means "a customer can buy this right now"
            'in_stock': purchasable,
        })
    return items


# ============================================================
# STOCK RESERVATIONS
#
# Stock used to be decremented in the Stripe webhook, i.e. after the card was
# charged. Two people buying the last unit would therefore both succeed at
# checkout and both be charged - one of them for something that cannot ship.
#
# Instead a hold is taken *before* the Stripe session is created, inside a
# transaction that locks the inventory rows. Holds expire on their own, so an
# abandoned checkout releases its stock without any cleanup job needing to run.
# ============================================================

HOLD_MINUTES = 30      # must be >= the Stripe Checkout session lifetime

# Item types that represent labour rather than a physical unit. They are never
# reserved, never sell out, and do not need a shipping address.
SERVICE_TYPES = ('service', 'lesson')


class OutOfStock(Exception):
    """Raised when a cart cannot be reserved. Carries the offending titles."""

    def __init__(self, unavailable):
        self.unavailable = unavailable
        super().__init__('; '.join(unavailable))


def reserve_cart(cart_items):
    """Atomically hold stock for every line in the cart.

    Returns a hold token to attach to the Stripe session.
    Raises OutOfStock if any line cannot be satisfied - nothing is held in that
    case, because the whole thing runs in one transaction.
    """
    wanted = {}
    for item in cart_items:
        key = item.get('listing_id') or (item.get('category'), item.get('title'))
        wanted[key] = wanted.get(key, 0) + 1

    token = uuid.uuid4().hex
    unavailable = []

    with get_cursor(commit=True) as cursor:
        for key, qty in wanted.items():
            # FOR UPDATE serialises concurrent checkouts on this row: the second
            # transaction blocks here until the first commits its hold, then sees
            # the updated availability.
            if isinstance(key, tuple):
                cursor.execute('''
                    SELECT id, title, stock, status, item_type FROM inventory
                    WHERE category = %s AND title = %s
                    FOR UPDATE
                ''', key)
            else:
                cursor.execute('''
                    SELECT id, title, stock, status, item_type FROM inventory
                    WHERE id = %s
                    FOR UPDATE
                ''', (key,))

            row = cursor.fetchone()
            if not row:
                unavailable.append(f"{key} (no longer listed)")
                continue

            listing_id, title, stock, status, item_type = row

            # Labour cannot sell out, so there is nothing to hold - only the
            # status matters.
            if item_type in SERVICE_TYPES:
                if status != 'active':
                    unavailable.append(f"{title} (not available)")
                continue

            cursor.execute('''
                SELECT COALESCE(SUM(quantity), 0) FROM reservations
                WHERE listing_id = %s AND status = 'held' AND expires_at > NOW()
            ''', (listing_id,))
            held = cursor.fetchone()[0]

            available = max(0, int(stock) - int(held))
            if status != 'active':
                unavailable.append(f"{title} (not available)")
            elif available < qty:
                unavailable.append(
                    f"{title} (only {available} left)" if available
                    else f"{title} (sold out)"
                )
            else:
                cursor.execute('''
                    INSERT INTO reservations (listing_id, hold_token, quantity, expires_at)
                    VALUES (%s, %s, %s, NOW() + INTERVAL '%s minutes')
                ''', (listing_id, token, qty, HOLD_MINUTES))

        if unavailable:
            # Raising rolls the transaction back, so no partial holds survive.
            raise OutOfStock(unavailable)

    return token


def price_cart(cart_items):
    """Resolve each cart line against the database.

    The browser sends titles and prices; trusting either would let a tampered
    cart set its own price. Only the id/title is used to look the row up - the
    amount charged always comes from the database.
    """
    counts = {}
    for item in cart_items:
        key = item.get('listing_id') or (item.get('category'), item.get('title'))
        counts[key] = counts.get(key, 0) + 1

    priced, missing = [], []
    with get_cursor() as cursor:
        for key, quantity in counts.items():
            if isinstance(key, tuple):
                cursor.execute(
                    "SELECT title, price, images, item_type FROM inventory "
                    "WHERE category = %s AND title = %s", key
                )
            else:
                cursor.execute(
                    "SELECT title, price, images, item_type FROM inventory WHERE id = %s",
                    (key,)
                )
            row = cursor.fetchone()
            if not row:
                missing.append(str(key))
                continue

            title, price, images, item_type = row
            if isinstance(images, str):
                try:
                    images = json.loads(images)
                except ValueError:
                    images = []
            first = normalize_image(images[0]) if images else ''

            priced.append({
                'title': title,
                'price': float(price),
                'image': first,
                'quantity': quantity,
                'item_type': item_type,
            })

    if missing:
        raise OutOfStock(missing)
    return priced


def attach_session_to_hold(token, session_id):
    """Record which Stripe session owns a hold, so the webhook can find it."""
    try:
        with get_cursor(commit=True) as cursor:
            cursor.execute(
                "UPDATE reservations SET checkout_session_id = %s WHERE hold_token = %s",
                (session_id, token),
            )
    except Exception as e:
        print(f"⚠️ Could not link hold {token} to session {session_id}: {e}")


def release_hold(token):
    """Give held stock back - used when Stripe fails after we reserved."""
    try:
        with get_cursor(commit=True) as cursor:
            cursor.execute(
                "UPDATE reservations SET status = 'released' "
                "WHERE hold_token = %s AND status = 'held'",
                (token,),
            )
    except Exception as e:
        print(f"⚠️ Could not release hold {token}: {e}")


def consume_hold(token, session_id):
    """Payment succeeded: turn the hold into an actual stock decrement.

    Matches any row that has not already been consumed - including one that was
    released or expired. A released hold plus a completed payment means the buyer
    came back and finished after all, and the sale is real either way; refusing to
    decrement there would oversell the item.

    Idempotent: only status <> 'consumed' rows are touched, so Stripe's webhook
    retries cannot decrement twice.
    """
    with get_cursor(commit=True) as cursor:
        cursor.execute('''
            SELECT listing_id, quantity FROM reservations
            WHERE hold_token = %s AND status <> 'consumed'
            FOR UPDATE
        ''', (token,))
        rows = cursor.fetchall()

        if not rows:
            print(f"ℹ️ Hold {token} already consumed; nothing to decrement")
            return 0

        for listing_id, quantity in rows:
            cursor.execute('''
                UPDATE inventory SET stock = GREATEST(0, stock - %s)
                WHERE id = %s
            ''', (quantity, listing_id))

        cursor.execute('''
            UPDATE reservations
            SET status = 'consumed', checkout_session_id = COALESCE(checkout_session_id, %s)
            WHERE hold_token = %s AND status <> 'consumed'
        ''', (session_id, token))

    return len(rows)


# The categories the shop started with, in display order: (slug, name, Game Boy
# menu label or None to reuse the name). Categories now live in the database
# and are managed in admin > Categories - this list only seeds that table the
# first time it is created, and stands in if it cannot be read. It must be
# defined above the init_db() call below, which runs at import time.
DEFAULT_CATEGORIES = [
    ('gameboy-color', 'Game Boy Color', 'Gameboy Color'),
    ('gameboy-advance', 'Game Boy Advance', 'Gameboy Advance'),
    ('gameboy-advance-sp', 'Game Boy Advance SP', 'Gameboy Advance SP'),
    ('nintendo-ds-lite', 'Nintendo DS Lite', None),
    ('mod-services', 'Mod & Repair Services', 'Mod My Console'),
    ('lessons', 'Modding Lessons', None),
]


# Database setup
def init_db():
    """Initialize the database with tables"""
    try:
        with get_cursor(commit=True) as cursor:
            # Create sellers table
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS sellers (
                    id SERIAL PRIMARY KEY,
                    name TEXT,
                    email TEXT NOT NULL,
                    item TEXT NOT NULL,
                    condition TEXT NOT NULL,
                    price TEXT NOT NULL,
                    shipping TEXT NOT NULL,
                    timestamp TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')

            # Create orders table
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS orders (
                    id SERIAL PRIMARY KEY,
                    session_id TEXT UNIQUE NOT NULL,
                    customer_email TEXT NOT NULL,
                    customer_name TEXT,
                    amount DECIMAL(10,2) NOT NULL,
                    items JSONB,
                    shipping_address JSONB,
                    status TEXT DEFAULT 'paid',
                    tracking_number TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    shipped_at TIMESTAMP
                )
            ''')

            # Inventory has lived only in Supabase until now - declaring it here keeps
            # the schema in version control. IF NOT EXISTS leaves existing data alone.
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS inventory (
                    id SERIAL PRIMARY KEY,
                    category TEXT NOT NULL,
                    title TEXT NOT NULL,
                    stock INTEGER NOT NULL,
                    price NUMERIC NOT NULL,
                    description TEXT,
                    images JSONB,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    slug TEXT
                )
            ''')

            # Which surface the buyer came through - 'console' for the Game Boy
            # interface, 'web' for the conventional pages. Recorded server-side
            # because analytics scripts get blocked and this number decides
            # whether the Game Boy earns its place.
            cursor.execute(
                "ALTER TABLE orders ADD COLUMN IF NOT EXISTS surface TEXT"
            )

            # Where the buyer came from. `ref` is the first source seen in the
            # last 30 days (what found them); `last_ref` is the most recent one
            # (what brought them back to buy).
            cursor.execute("ALTER TABLE orders ADD COLUMN IF NOT EXISTS ref TEXT")
            cursor.execute("ALTER TABLE orders ADD COLUMN IF NOT EXISTS last_ref TEXT")

            # First-party visit and interaction log. No IP address, no user
            # agent, no cookies - a random per-tab session id is the only link
            # between events, and it cannot identify anyone.
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS events (
                    id         BIGSERIAL PRIMARY KEY,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    session_id TEXT,
                    type       TEXT NOT NULL,
                    path       TEXT,
                    ref        TEXT,
                    listing    TEXT,
                    detail     TEXT,
                    surface    TEXT
                )
            ''')
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS events_created_idx ON events (created_at)"
            )
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS events_type_created_idx ON events (type, created_at)"
            )
            # Keep six months. Old visit data has little value and the table
            # should not grow without bound.
            cursor.execute(
                "DELETE FROM events WHERE created_at < NOW() - INTERVAL '180 days'"
            )

            # Slug backs the public /listing/<slug> URLs.
            cursor.execute("ALTER TABLE inventory ADD COLUMN IF NOT EXISTS slug TEXT")
            cursor.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS inventory_slug_idx ON inventory (slug)"
            )

            # Manual availability override, independent of quantity. 'active' is for
            # sale; 'hidden' hides it everywhere; 'coming_soon' shows it but blocks
            # purchase. Quantity still lives in `stock`.
            cursor.execute(
                "ALTER TABLE inventory ADD COLUMN IF NOT EXISTS status TEXT NOT NULL DEFAULT 'active'"
            )

            # What is being sold. 'console' is a physical unit with finite stock;
            # 'service' (send-in repair/mod) and 'lesson' (booked time) are labour,
            # so they never run out and are never reserved.
            cursor.execute(
                "ALTER TABLE inventory ADD COLUMN IF NOT EXISTS item_type TEXT NOT NULL DEFAULT 'console'"
            )

            # Stock holds placed when a checkout starts. Without these, two people
            # can both pay for the last unit: stock was only decremented in the
            # webhook, which runs after the card is charged.
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS reservations (
                    id SERIAL PRIMARY KEY,
                    listing_id INTEGER NOT NULL REFERENCES inventory(id) ON DELETE CASCADE,
                    hold_token TEXT NOT NULL,
                    checkout_session_id TEXT,
                    quantity INTEGER NOT NULL DEFAULT 1,
                    status TEXT NOT NULL DEFAULT 'held',
                    expires_at TIMESTAMP NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            cursor.execute('''
                CREATE INDEX IF NOT EXISTS reservations_active_idx
                ON reservations (listing_id, status, expires_at)
            ''')
            cursor.execute('''
                CREATE INDEX IF NOT EXISTS reservations_token_idx
                ON reservations (hold_token)
            ''')

            # Newsletter signups. Kept locally as well as in Resend so the list
            # survives a provider change and so a failed API call never loses an
            # address that someone actually typed in.
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS subscribers (
                    id SERIAL PRIMARY KEY,
                    email TEXT NOT NULL UNIQUE,
                    source TEXT DEFAULT 'site',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    synced_at TIMESTAMP,
                    unsubscribed_at TIMESTAMP
                )
            ''')

            # Shop categories, managed in admin > Categories. `position` is the
            # order on /shop and in the Game Boy's shop menu; `slug` is
            # permanent because listings, URLs and analytics refer to it.
            cursor.execute("SELECT to_regclass('categories') IS NULL")
            categories_are_new = cursor.fetchone()[0]
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS categories (
                    id SERIAL PRIMARY KEY,
                    slug TEXT NOT NULL UNIQUE,
                    label TEXT NOT NULL,
                    menu_label TEXT,
                    position INTEGER NOT NULL DEFAULT 0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            # Seed only when the table is first created. Re-seeding on every
            # start would bring back categories that were deliberately deleted.
            # Any category a listing already uses is kept too, so none is
            # orphaned.
            if categories_are_new:
                seed = list(DEFAULT_CATEGORIES)
                known = {slug for slug, _, _ in seed}
                cursor.execute(
                    "SELECT DISTINCT category FROM inventory WHERE category <> '' ORDER BY 1"
                )
                seed += [(slug, slug.replace('-', ' ').title(), None)
                         for (slug,) in cursor.fetchall() if slug not in known]
                for position, (slug, label, menu_label) in enumerate(seed):
                    cursor.execute('''
                        INSERT INTO categories (slug, label, menu_label, position)
                        VALUES (%s, %s, %s, %s)
                        ON CONFLICT (slug) DO NOTHING
                    ''', (slug, label, menu_label, position))

            # Every table this app creates gets RLS with no policy: deny-all
            # through Supabase's public REST API. Supabase grants the `anon`
            # role on new tables by default, so without this a freshly created
            # table is world-readable and writable to anyone holding the anon
            # key. Flask connects as the owner and is unaffected.
            for table in ('sellers', 'orders', 'inventory', 'reservations',
                          'subscribers', 'events', 'categories'):
                cursor.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")

        backfill_slugs()
        print("✅ Database tables initialized")
    except Exception as e:
        # A sleeping database at boot must not take the whole app down - the pool
        # rebuilds itself on the first request that gets through.
        print(f"⚠️ Skipping database init ({e}); will retry on first request")


# Initialize database on startup
init_db()


def hero_demo_data(items):
    """What the landing page's demo Game Boy plays, from live data.

    The shop menu as the Game Boy shows it - same names, same counts - and up
    to three in-stock consoles that have a photo, each with the rows of its
    category menu. static/hero-demo.js scripts the button presses.
    """
    categories = load_categories()
    in_menu = {c['slug'] for c in categories}
    counts = {}
    for i in items:
        if i['in_stock']:
            counts[i['category']] = counts.get(i['category'], 0) + 1

    listings = []
    for item in items:
        if len(listings) == 3:
            break
        if (not item['in_stock'] or item['is_service'] or not item['images']
                or item['category'] not in in_menu):
            continue
        rows = [i for i in items if i['category'] == item['category']][:8]
        if item not in rows:
            continue
        listings.append({
            'slug': item['slug'],
            'title': item['title'],
            'price': item['price'],
            'image': item['images'][0],
            'category': item['category'],
            'row': rows.index(item),
            'rows': [{'title': r['title'], 'price': r['price'], 'left': r['available'],
                      'in_stock': r['in_stock'], 'service': r['is_service'],
                      'coming_soon': r['status'] == 'coming_soon'} for r in rows],
        })

    return {
        'categories': [{'slug': c['slug'], 'label': c['menu_label'] or c['label'],
                        'count': counts.get(c['slug'], 0)} for c in categories],
        'listings': listings,
    }


@app.route('/')
def index():
    """The conventional landing page - what search engines, ads and shared links
    land on. The Game Boy experience is one click away at /play."""
    try:
        items = fetch_listings()
    except Exception as e:
        print(f"Error loading landing page: {e}")
        items = []

    featured = [i for i in items if i['in_stock'] and not i['is_service']][:3]
    services = [i for i in items if i['in_stock'] and i['is_service']][:3]

    return render_template(
        'landing.html',
        featured=featured,
        services=services,
        available_count=count_available_consoles(items),
        review_profiles=[p for p in REVIEW_PROFILES if p['url']],
        hero_demo=hero_demo_data(items),
        canonical=site_root() + '/',
    )


# The Game Boy experience.
#
# NOT /console: Werkzeug's debug middleware reserves that path for its own
# interactive debugger (console_path defaults to "/console") and intercepts it
# before Flask routing. The route would work under gunicorn and break under
# `python app.py`, which is the worst kind of bug to own.
PRIVACY_UPDATED = 'October 1, 2026'


@app.route('/privacy')
def privacy():
    return render_template('privacy.html', updated=PRIVACY_UPDATED,
                           canonical=site_root() + '/privacy')


@app.route('/play')
def console_view():
    # The shop menu's categories, embedded so the menu renders without waiting
    # on a request. The Game Boy uses the shorter menu label where one is set.
    shop_categories = [{'slug': c['slug'], 'label': c['menu_label'] or c['label']}
                       for c in load_categories()]
    return render_template('index.html', stripe_key=STRIPE_PUBLISHABLE_KEY,
                           canonical=site_root() + '/play',
                           shop_categories=shop_categories)


@app.route('/health')
def health():
    """Liveness probe for Render.

    Deliberately does NOT touch the database. Render restarts the service when
    this fails, and restarting cannot fix a sleeping or unreachable Supabase -
    it would just cycle the app and take the static pages down with it.

    Use /health/db for a real dependency check (monitoring, not the probe).
    """
    return jsonify({'status': 'ok'}), 200


@app.route('/health/db')
def health_db():
    """Deep check: is the database actually reachable right now?"""
    try:
        with get_cursor() as cursor:
            cursor.execute('SELECT 1')
            cursor.fetchone()
        return jsonify({'status': 'ok', 'database': 'up'}), 200
    except Exception as e:
        return jsonify({'status': 'degraded', 'database': 'down',
                        'error': str(e)[:200]}), 503


@app.route('/submit-seller', methods=['POST'])
def submit_seller():
    """Handle seller submission from chatbot"""
    try:
        data = request.get_json()

        # Extract data
        item = data.get('item', '')
        condition = data.get('condition', '')
        price = data.get('price', '')
        shipping = data.get('shipping', '')
        email = data.get('email', '')
        timestamp = data.get('timestamp', datetime.now().isoformat())

        # Validate required fields
        if not all([item, condition, price, shipping, email]):
            return jsonify({'error': 'Missing required fields'}), 400

        # Save to database (PostgreSQL)
        with get_cursor(commit=True) as cursor:
            cursor.execute('''
                INSERT INTO sellers (email, item, condition, price, shipping, timestamp)
                VALUES (%s, %s, %s, %s, %s, %s) RETURNING id
            ''', (email, item, condition, price, shipping, timestamp))
            submission_id = cursor.fetchone()[0]

        print(f"New seller submission #{submission_id}:")
        print(f"  Item: {item}")
        print(f"  Condition: {condition}")
        print(f"  Price: {price}")
        print(f"  Shipping: {shipping}")
        print(f"  Email: {email}")

        # Notify by email. Best effort - the row is already saved either way, and
        # reply_to means hitting Reply in the inbox goes straight to the seller.
        send_email(
            subject=f'New Seller Submission: {item}',
            body=(
                f"New submission #{submission_id}\n\n"
                f"Item:      {item}\n"
                f"Condition: {condition}\n"
                f"Price:     {price}\n"
                f"Shipping:  {shipping}\n"
                f"Email:     {email}\n"
                f"Received:  {timestamp}\n"
            ),
            reply_to=email,
        )

        return jsonify({
            'success': True,
            'message': 'Submission received!',
            'submission_id': submission_id
        })

    except Exception as e:
        print(f"Error in submit_seller: {str(e)}")
        import traceback
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500


@app.route('/view-submissions')
@login_required
def view_submissions():
    """View all seller submissions - for admin use"""
    try:
        with get_cursor() as cursor:
            cursor.execute('SELECT * FROM sellers ORDER BY created_at DESC')
            submissions = cursor.fetchall()

        # Format for display
        formatted_submissions = []
        for sub in submissions:
            formatted_submissions.append({
                'id': sub[0],
                'email': sub[2],
                'item': sub[3],
                'condition': sub[4],
                'price': sub[5],
                'shipping': sub[6],
                'timestamp': sub[7],
                'created_at': str(sub[8])
            })

        return jsonify({'submissions': formatted_submissions})

    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/create-checkout-session', methods=['POST'])
def create_checkout_session():
    try:
        data = request.get_json()
        cart_items = data.get('cart', [])

        # Which experience the buyer came through. Trusted only as a label - it
        # affects reporting, never pricing or availability.
        surface = data.get('surface')
        if surface not in ('console', 'web'):
            surface = 'unknown'

        # Where the buyer came from. Same rule as surface: a reporting label
        # only, never trusted for pricing or availability.
        ref = clean_ref(data.get('ref')) or 'direct'
        last_ref = clean_ref(data.get('last_ref')) or ref

        if not cart_items:
            return jsonify({'error': 'Cart is empty'}), 400

        # Hold the stock BEFORE sending anyone to Stripe. If someone else got
        # there first this fails now, with an explanation, instead of charging
        # a card for something that cannot ship.
        try:
            hold_token = reserve_cart(cart_items)
        except OutOfStock as e:
            return jsonify({
                'error': 'Some items are no longer available',
                'unavailable': e.unavailable,
            }), 409

        # Prices come from the database, never from the browser - otherwise a
        # tampered cart could set its own price.
        try:
            priced = price_cart(cart_items)
        except OutOfStock as e:
            release_hold(hold_token)
            return jsonify({
                'error': 'Some items are no longer available',
                'unavailable': e.unavailable,
            }), 409

        root = site_root()
        line_items = []
        for entry in priced:
            product = {'name': entry['title']}
            if entry['image']:
                image = entry['image']
                product['images'] = [image if image.startswith('http') else root + image]
            line_items.append({
                'price_data': {
                    'currency': 'usd',
                    'product_data': product,
                    'unit_amount': int(round(entry['price'] * 100)),
                },
                'quantity': entry['quantity'],
            })

        # A cart of nothing but labour has nothing to post, so don't make the
        # customer type an address they don't need to give.
        needs_shipping = any(e['item_type'] not in SERVICE_TYPES for e in priced)

        session_args = {
            'payment_method_types': ['card'],
            'line_items': line_items,
            'mode': 'payment',
            'success_url': site_root() + '/success?session_id={CHECKOUT_SESSION_ID}',
            # The token rides along so backing out of Stripe frees the stock
            # immediately instead of waiting for the hold to time out.
            'cancel_url': site_root() + '/cancel?hold=' + hold_token,
            'expires_at': int(datetime.now().timestamp()) + HOLD_MINUTES * 60,
            # The hold token is all the webhook needs - the reservation rows
            # already record exactly which listings to decrement.
            'metadata': {'hold_token': hold_token, 'surface': surface,
                         'ref': ref, 'last_ref': last_ref},
        }
        if needs_shipping:
            session_args['shipping_address_collection'] = {
                'allowed_countries': ['US', 'CA'],
            }

        try:
            checkout_session = stripe.checkout.Session.create(**session_args)
        except Exception:
            release_hold(hold_token)
            raise

        attach_session_to_hold(hold_token, checkout_session.id)

        # The client keeps the token so that if the buyer closes the Stripe tab
        # and comes back, the hold can be dropped on the next page load.
        return jsonify({'sessionId': checkout_session.id, 'holdToken': hold_token})

    except Exception as e:
        print(f"Checkout error: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/stock')
def get_stock():
    """Return current inventory, grouped by category, for the Game Boy UI."""
    try:
        listings = {}
        for item in fetch_listings():
            listings.setdefault(item['category'], []).append({
                'id': item['id'],
                'title': item['title'],
                'description': item['description'],
                'price': item['price'],
                # `stock` is what the UI should judge availability by: it already
                # has in-flight checkout holds subtracted.
                'stock': item['available'],
                'status': item['status'],
                'in_stock': item['in_stock'],
                'is_service': item['is_service'],
                'item_type': item['item_type'],
                'images': item['images'],
                'slug': item['slug'],
            })
        return jsonify(listings)

    except Exception as e:
        print(f"Error fetching stock: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500


# ============================================================
# PUBLIC CRAWLABLE PAGES
# The Game Boy UI renders client-side, so search engines and link
# previews see nothing there. These server-rendered pages give every
# listing a real URL, real HTML and structured data.
# ============================================================

# Marketplace profiles shown as social proof on the landing page.
#
# Keep `rating` and `sales` accurate and update them when they move - the FTC's
# consumer review rule applies to small sellers, and a checkable number builds
# more trust than a superlative anyway. Set `url` to '' to hide a platform.
REVIEW_PROFILES = [
    {
        'platform': 'Mercari',
        'url': 'https://www.mercari.com/u/gameboyretreat/',              # https://www.mercari.com/u/<your-id>/
        'rating': '4.9',           # '4.9'
        'sales': '145',            # '312'
        'note': 'Most of my sales happen here',
    },
    {
        'platform': 'OfferUp',
        'url': '',              # https://offerup.com/p/<your-id>
        'rating': '',
        'sales': '',
        'note': 'Local pickup around Stockton',
    },
    {
        'platform': 'Facebook Marketplace',
        'url': '',              # link to your seller profile
        'rating': '',
        'sales': '',
        'note': 'Local sales and trades',
    },
]


ITEM_TYPES = {
    'console': 'Console (physical, finite stock)',
    'service': 'Service (send-in repair or mod)',
    'lesson': 'Lesson (booked time)',
}


# ============================================================
# CATEGORIES
# Managed in admin > Categories. `position` is the order on /shop and in the
# Game Boy's shop menu, so the two always match.
# ============================================================

def load_categories(strict=False):
    """Every category in display order: dicts of id, slug, label, menu_label
    and position.

    Public pages fall back to DEFAULT_CATEGORIES if the table cannot be read,
    rather than losing their menus. The admin page passes strict=True -
    editing a stand-in list would only fail later.
    """
    try:
        with get_cursor() as cursor:
            cursor.execute('SELECT id, slug, label, menu_label, position '
                           'FROM categories ORDER BY position, id')
            rows = cursor.fetchall()
    except Exception as e:
        if strict:
            raise
        print(f"⚠️ Could not read categories ({e}); using the defaults")
        return [{'id': None, 'slug': slug, 'label': label, 'menu_label': menu_label,
                 'position': n}
                for n, (slug, label, menu_label) in enumerate(DEFAULT_CATEGORIES)]
    return [{'id': i, 'slug': s, 'label': l, 'menu_label': m, 'position': p}
            for i, s, l, m, p in rows]


def category_labels(categories=None):
    """{slug: name} in display order - the shape the templates use."""
    if categories is None:
        categories = load_categories()
    return {c['slug']: c['label'] for c in categories}


def category_label(slug, labels):
    return labels.get(slug) or slug.replace('-', ' ').title()


def renumber_categories(cursor, ids):
    """Store positions 0..n-1 in the order given, in one statement."""
    cursor.execute('''
        UPDATE categories c SET position = v.n - 1
        FROM unnest(%s::int[]) WITH ORDINALITY AS v(id, n)
        WHERE c.id = v.id
    ''', (list(ids),))


def add_category(label, menu_label=None):
    """Insert a category with a fresh slug and return the slug.

    It goes just above the first category that holds only services or
    lessons, so a new console line does not land under Modding Lessons.
    """
    with get_cursor(commit=True) as cursor:
        cursor.execute('SELECT slug FROM categories')
        taken = {r[0] for r in cursor.fetchall()}
        base = slugify(label)
        slug, n = base, 2
        while slug in taken:
            slug = f"{base}-{n}"
            n += 1

        cursor.execute('''
            SELECT MIN(c.position) FROM categories c
            WHERE EXISTS (SELECT 1 FROM inventory i WHERE i.category = c.slug)
              AND NOT EXISTS (SELECT 1 FROM inventory i
                              WHERE i.category = c.slug AND i.item_type NOT IN %s)
        ''', (SERVICE_TYPES,))
        position = cursor.fetchone()[0]
        if position is None:
            cursor.execute('SELECT COALESCE(MAX(position) + 1, 0) FROM categories')
            position = cursor.fetchone()[0]

        cursor.execute('UPDATE categories SET position = position + 1 WHERE position >= %s',
                       (position,))
        cursor.execute('''
            INSERT INTO categories (slug, label, menu_label, position)
            VALUES (%s, %s, %s, %s)
        ''', (slug, label, menu_label, position))
    return slug


def categories_page(errors=None, status=200):
    """The admin categories page, each category with its listing count."""
    try:
        categories = load_categories(strict=True)
        with get_cursor() as cursor:
            cursor.execute('SELECT category, count(*) FROM inventory GROUP BY category')
            counts = dict(cursor.fetchall())
    except Exception as e:
        return f"Database error: {e}", 500
    for c in categories:
        c['listings'] = counts.get(c['slug'], 0)
    return render_template(
        'admin_categories.html',
        categories=categories,
        errors=errors or [],
        form=request.form if errors and request.method == 'POST' else None,
    ), status


@app.route('/admin/categories', methods=['GET', 'POST'])
@login_required
def admin_categories():
    """Every category with its listing count; POST adds a new one."""
    if request.method == 'POST':
        label = (request.form.get('label') or '').strip()
        menu_label = (request.form.get('menu_label') or '').strip() or None
        if not label:
            return categories_page(['Give the new category a name.'], 400)
        try:
            slug = add_category(label, menu_label)
        except Exception as e:
            return categories_page([f'Could not add the category: {e}'], 500)
        print(f"✅ Added category '{label}' ({slug})")
        return redirect('/admin/categories')
    return categories_page()


@app.route('/admin/categories/<int:category_id>/update', methods=['POST'])
@login_required
def admin_category_update(category_id):
    """Rename a category. The slug never changes - listings, shop links and
    analytics all refer to it."""
    label = (request.form.get('label') or '').strip()
    menu_label = (request.form.get('menu_label') or '').strip() or None
    if not label:
        return categories_page(['A category needs a name.'], 400)
    try:
        with get_cursor(commit=True) as cursor:
            cursor.execute(
                'UPDATE categories SET label = %s, menu_label = %s WHERE id = %s',
                (label, menu_label, category_id),
            )
    except Exception as e:
        return f"Database error: {e}", 500
    return redirect('/admin/categories')


@app.route('/admin/categories/<int:category_id>/move', methods=['POST'])
@login_required
def admin_category_move(category_id):
    """Swap a category with its neighbour above or below."""
    step = -1 if request.form.get('direction') == 'up' else 1
    try:
        with get_cursor(commit=True) as cursor:
            cursor.execute('SELECT id FROM categories ORDER BY position, id')
            ids = [r[0] for r in cursor.fetchall()]
            if category_id in ids:
                i = ids.index(category_id)
                if 0 <= i + step < len(ids):
                    ids[i], ids[i + step] = ids[i + step], ids[i]
                    renumber_categories(cursor, ids)
    except Exception as e:
        return f"Database error: {e}", 500
    return redirect('/admin/categories')


@app.route('/admin/categories/<int:category_id>/delete', methods=['POST'])
@login_required
def admin_category_delete(category_id):
    """Delete a category that has no listings. One with listings - hidden ones
    included - is refused, because they would drop out of every menu."""
    try:
        with get_cursor(commit=True) as cursor:
            cursor.execute('SELECT slug, label FROM categories WHERE id = %s', (category_id,))
            row = cursor.fetchone()
            if not row:
                return redirect('/admin/categories')
            slug, label = row
            cursor.execute('SELECT count(*) FROM inventory WHERE category = %s', (slug,))
            in_use = cursor.fetchone()[0]
            if not in_use:
                cursor.execute('DELETE FROM categories WHERE id = %s', (category_id,))
                cursor.execute('SELECT id FROM categories ORDER BY position, id')
                renumber_categories(cursor, [r[0] for r in cursor.fetchall()])
    except Exception as e:
        return f"Database error: {e}", 500

    if in_use:
        them = 'it' if in_use == 1 else 'them'
        return categories_page(
            [f'"{label}" still has {in_use} listing{"" if in_use == 1 else "s"}. '
             f'Move {them} to another category or delete {them} first.'], 409)
    print(f"🗑️ Deleted category '{label}' ({slug})")
    return redirect('/admin/categories')


def count_available_consoles(items):
    """Consoles a customer can buy right now - the number the landing page and
    the shop quote. Services and lessons are labour, not consoles, and never
    count; neither does anything sold out or coming soon."""
    return sum(1 for i in items if i['in_stock'] and not i['is_service'])


@app.route('/shop')
def shop_index():
    """Crawlable grid of every listing, sold ones included."""
    try:
        items = fetch_listings()
    except Exception as e:
        print(f"Error loading shop index: {e}")
        items = []

    labels = category_labels()
    # Section order is the category order from admin > Categories, where the
    # services and lessons sit below the consoles. Anything not in the table
    # goes last.
    order = {slug: n for n, slug in enumerate(labels)}

    grouped = {}
    for item in items:
        grouped.setdefault(item['category'], []).append(item)

    # Note: not named 'items' - Jinja would resolve category.items to the dict's
    # built-in method instead of this key.
    categories = [
        {'slug': slug, 'label': category_label(slug, labels), 'products': group}
        for slug, group in sorted(grouped.items(),
                                  key=lambda entry: (order.get(entry[0], len(order)), entry[0]))
    ]

    return render_template(
        'shop.html',
        categories=categories,
        available_count=count_available_consoles(items),
        canonical=site_root() + '/shop',
    )


@app.route('/listing/<slug>')
def listing_detail(slug):
    """One listing, with Open Graph tags and Product structured data."""
    try:
        items = fetch_listings()
    except Exception as e:
        print(f"Error loading listing {slug}: {e}")
        return render_template('listing_missing.html'), 503

    item = next((i for i in items if i['slug'] == slug), None)
    if not item:
        return render_template('listing_missing.html'), 404

    root = site_root()
    canonical = f"{root}/listing/{item['slug']}"
    images_abs = [root + p if p.startswith('/') else p for p in item['images']]
    labels = category_labels()

    # Schema.org Product - this is what produces price and availability in
    # Google results rather than a plain blue link.
    structured_data = {
        '@context': 'https://schema.org',
        '@type': 'Product',
        'name': item['title'],
        'description': item['description'],
        'image': images_abs,
        'category': category_label(item['category'], labels),
        'itemCondition': 'https://schema.org/RefurbishedCondition',
        'brand': {'@type': 'Brand', 'name': 'Gameboy Retreat'},
        'offers': {
            '@type': 'Offer',
            'url': canonical,
            'priceCurrency': 'USD',
            'price': f"{item['price']:.2f}",
            'availability': ('https://schema.org/InStock' if item['in_stock']
                             else 'https://schema.org/OutOfStock'),
            'seller': {'@type': 'Organization', 'name': 'Gameboy Retreat'},
        },
    }

    related = [i for i in items
               if i['category'] == item['category'] and i['slug'] != item['slug']][:4]

    return render_template(
        'listing.html',
        item=item,
        category_name=category_label(item['category'], labels),
        canonical=canonical,
        og_image=images_abs[0] if images_abs else '',
        # Rendered |safe inside <script>, so a description containing "</script>"
        # or "<!--" could end or derail the tag. JSON reads the \u escapes back
        # as the same characters (the same trick as Django's json_script).
        structured_data=json.dumps(structured_data, indent=2)
            .replace('&', '\\u0026').replace('<', '\\u003c').replace('>', '\\u003e'),
        related=related,
    )


@app.context_processor
def inject_globals():
    """Values every template can use: the footer year, and the Search Console
    verification token when one is configured."""
    return {
        'year': datetime.now().year,
        'google_site_verification': GOOGLE_SITE_VERIFICATION,
    }


@app.route('/robots.txt')
def robots_txt():
    root = site_root()
    body = '\n'.join([
        'User-agent: *',
        'Allow: /',
        'Disallow: /admin',
        'Disallow: /api/',
        'Disallow: /view-submissions',
        'Disallow: /success',
        'Disallow: /cancel',
        '',
        f'Sitemap: {root}/sitemap.xml',
        '',
    ])
    return Response(body, mimetype='text/plain')


@app.route('/sitemap.xml')
def sitemap_xml():
    """Every URL worth indexing. Google reads this instead of guessing."""
    root = site_root()
    try:
        items = fetch_listings()
    except Exception as e:
        print(f"Error building sitemap: {e}")
        items = []

    urls = [
        {'loc': f'{root}/', 'priority': '1.0', 'changefreq': 'weekly'},
        {'loc': f'{root}/shop', 'priority': '0.9', 'changefreq': 'daily'},
        {'loc': f'{root}/play', 'priority': '0.5', 'changefreq': 'monthly'},
        {'loc': f'{root}/privacy', 'priority': '0.2', 'changefreq': 'yearly'},
    ]
    urls += [
        {'loc': f"{root}/listing/{i['slug']}", 'priority': '0.8', 'changefreq': 'weekly'}
        for i in items
    ]

    xml = ['<?xml version="1.0" encoding="UTF-8"?>',
           '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">']
    for u in urls:
        xml.append('  <url>')
        xml.append(f"    <loc>{escape(u['loc'])}</loc>")
        xml.append(f"    <changefreq>{u['changefreq']}</changefreq>")
        xml.append(f"    <priority>{u['priority']}</priority>")
        xml.append('  </url>')
    xml.append('</urlset>')

    return Response('\n'.join(xml), mimetype='application/xml')


@app.route('/success')
def success():
    session_id = request.args.get('session_id')

    if session_id:
        try:
            session = stripe.checkout.Session.retrieve(session_id)
            if session.payment_status == "paid":
                return render_template('success.html', session_id=session_id)
            else:
                return redirect('/cancel')
        except Exception as e:
            print(f"Session error: {e}")
            return redirect('/')
    else:
        return render_template('success.html', session_id=None)


@app.route('/cancel')
def cancel():
    # Stripe sends the buyer here when they back out. Releasing on arrival means
    # the item is back on sale in seconds rather than after the hold expires.
    token = request.args.get('hold')
    if token:
        release_hold(token)
        print(f"↩️ Buyer backed out; released hold {token}")
    return render_template('cancel.html')


# ============================================================
# FIRST-PARTY ANALYTICS
#
# GA4 is still on the site, but ad blockers drop it for a large share of a
# gaming audience, and its reports take learning. This logs the handful of
# events that answer the questions that actually matter - where did they come
# from, what did they look at, what did they buy - into our own database.
# ============================================================

EVENT_TYPES = {
    'pageview', 'cta', 'category_view', 'listing_view',
    'add_to_cart', 'checkout_start', 'signup',
}

REF_RE = re.compile(r'^[a-z0-9][a-z0-9_.:-]{0,59}$')
SESSION_RE = re.compile(r'^[a-zA-Z0-9]{8,40}$')

BOT_MARKERS = (
    'bot', 'crawl', 'spider', 'slurp', 'facebookexternalhit', 'preview',
    'headless', 'lighthouse', 'pingdom', 'uptime', 'curl', 'wget', 'python-requests',
)

EVENT_RATE_LIMIT = 120          # per IP per minute, per worker
_event_hits = {}
_event_lock = threading.Lock()


def clean_ref(value):
    """Normalise a source label, or None if it is not a plausible one."""
    if not value:
        return None
    value = str(value).strip().lower()[:60]
    return value if REF_RE.match(value) else None


def _event_rate_limited(ip):
    now = datetime.now().timestamp()
    with _event_lock:
        hits = [t for t in _event_hits.get(ip, []) if now - t < 60]
        if len(hits) >= EVENT_RATE_LIMIT:
            _event_hits[ip] = hits
            return True
        hits.append(now)
        _event_hits[ip] = hits
        # Stop the dict growing forever on a long-lived worker.
        if len(_event_hits) > 5000:
            _event_hits.clear()
        return False


@app.route('/api/event', methods=['POST'])
def api_event():
    """Record one interaction. Always returns 204 - analytics must never be the
    reason a page errors, and there is nothing useful to tell the client."""
    ua = (request.headers.get('User-Agent') or '').lower()
    if not ua or any(m in ua for m in BOT_MARKERS):
        return '', 204
    if _event_rate_limited(request.remote_addr or 'unknown'):
        return '', 204

    # sendBeacon posts with whatever content type it likes; parse regardless.
    data = request.get_json(silent=True, force=True) or {}
    event_type = data.get('type')
    if event_type not in EVENT_TYPES:
        return '', 204

    session_id = data.get('session_id') or ''
    if not SESSION_RE.match(session_id):
        session_id = None

    path = str(data.get('path') or '')[:200]
    if not path.startswith('/'):
        path = None

    surface = data.get('surface')
    if surface not in ('console', 'web'):
        surface = None

    try:
        with get_cursor(commit=True) as cursor:
            cursor.execute('''
                INSERT INTO events (session_id, type, path, ref, listing, detail, surface)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
            ''', (
                session_id,
                event_type,
                path,
                clean_ref(data.get('ref')),
                (str(data.get('listing'))[:120] if data.get('listing') else None),
                (str(data.get('detail'))[:120] if data.get('detail') else None),
                surface,
            ))
    except Exception as e:
        print(f"⚠️ Event not recorded: {e}")

    return '', 204


@app.route('/api/subscribe', methods=['POST'])
def api_subscribe():
    """Newsletter signup, from either the web pages or the Game Boy UI."""
    payload = request.get_json(silent=True) or request.form
    email = payload.get('email', '')
    source = payload.get('source', 'site')

    ok, message = add_to_audience(email, source=source)

    # A normal form post (no JS) gets a redirect back with a flash-ish query arg.
    if not request.is_json and request.form:
        return redirect('/shop?subscribed=1' if ok else '/shop?subscribed=0')

    return jsonify({'ok': ok, 'message': message}), (200 if ok else 400)


@app.route('/api/release-hold', methods=['POST'])
def api_release_hold():
    """Best-effort release from the browser (sendBeacon on tab close).

    Releasing stock is the safe direction - the worst a bad actor achieves is
    putting an item back on sale - so this needs no authentication. A payment
    that completes afterwards still decrements, because consume_hold matches
    released rows too.
    """
    token = (request.get_json(silent=True) or {}).get('hold_token')
    if not token:
        token = request.form.get('hold_token')
    if token:
        release_hold(token)
    return jsonify({'released': bool(token)})


@app.route('/webhook', methods=['POST'])
def webhook():
    payload = request.get_data()
    sig_header = request.headers.get('Stripe-Signature')
    webhook_secret = os.getenv('STRIPE_WEBHOOK_SECRET')

    try:
        event = stripe.Webhook.construct_event(
            payload, sig_header, webhook_secret
        )

        if event['type'] == 'checkout.session.completed':
            session = event['data']['object']

            # Extract order details
            customer_email = session.get('customer_details', {}).get('email', 'N/A')
            amount_total = session.get('amount_total', 0) / 100
            session_id = session.get('id', 'N/A')

            # Get shipping
            shipping = session.get('shipping_details', {})
            shipping_name = shipping.get('name', 'N/A') if shipping else 'N/A'
            shipping_address = shipping.get('address', {}) if shipping else {}

            meta = session.get('metadata') or {}
            hold_token = meta.get('hold_token')
            surface = meta.get('surface', 'unknown')
            ref = clean_ref(meta.get('ref')) or 'direct'
            last_ref = clean_ref(meta.get('last_ref')) or ref

            # What was actually bought, straight from Stripe rather than the
            # browser's version of the cart.
            try:
                line_items = stripe.checkout.Session.list_line_items(session_id, limit=100)
                purchased = [
                    {'title': li.get('description'),
                     'quantity': li.get('quantity'),
                     'amount': (li.get('amount_total') or 0) / 100}
                    for li in line_items.get('data', [])
                ]
            except Exception as e:
                print(f"⚠️ Could not read line items for {session_id}: {e}")
                purchased = []

            try:
                with get_cursor(commit=True) as cursor:
                    cursor.execute('''
                        INSERT INTO orders (session_id, customer_email, customer_name,
                                            amount, shipping_address, items, surface,
                                            ref, last_ref)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT (session_id) DO NOTHING
                    ''', (
                        session_id,
                        customer_email,
                        shipping_name,
                        amount_total,
                        json.dumps(shipping_address),
                        json.dumps(purchased),
                        surface,
                        ref,
                        last_ref,
                    ))
                print(f"✅ Order saved: {session_id}")
            except Exception as db_err:
                print(f"⚠️ Order save failed: {db_err}")

            # Turn the hold into a real decrement. Idempotent, because Stripe
            # retries webhooks and this must not run twice.
            if hold_token:
                try:
                    n = consume_hold(hold_token, session_id)
                    print(f"✅ Consumed hold {hold_token} ({n} line(s))")
                except Exception as e:
                    print(f"⚠️ Could not consume hold {hold_token}: {e}")
            else:
                print(f"⚠️ Session {session_id} had no hold_token; stock not adjusted")

            print(f"✅ Payment successful for: {customer_email} - ${amount_total}")

        elif event['type'] in ('checkout.session.expired', 'checkout.session.async_payment_failed'):
            # Abandoned or failed checkout - hand the stock back immediately
            # rather than waiting for the hold to time out.
            session = event['data']['object']
            hold_token = (session.get('metadata') or {}).get('hold_token')
            if hold_token:
                release_hold(hold_token)
                print(f"↩️ Released hold {hold_token} ({event['type']})")

        return jsonify({'status': 'success'})

    except Exception as e:
        print(f"Webhook error: {str(e)}")
        return jsonify({'error': str(e)}), 400


@app.errorhandler(404)
def page_not_found(e):
    # A real not-found page. This used to render the Game Boy with a 404 status,
    # so a mistyped URL looked like the homepage with no explanation.
    return render_template('not_found.html'), 404


if __name__ == '__main__':
    # Bind to loopback only. With debug=True the Werkzeug debugger is a full
    # Python REPL behind a PIN - on 0.0.0.0 anyone on the same network can reach
    # it. Set DEV_HOST=0.0.0.0 deliberately when testing from a phone.
    host = os.environ.get('DEV_HOST', '127.0.0.1')
    port = int(os.environ.get('DEV_PORT', '5000'))
    debug = os.environ.get('DEV_DEBUG', '1') != '0'
    app.run(debug=debug, host=host, port=port)