from flask import Flask, render_template, request, jsonify, redirect, Response
from markupsafe import escape
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

app.secret_key = os.getenv('SECRET_KEY', 'dev-secret-key-change-in-production')

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

db_pool = None
_pool_lock = threading.Lock()


def _ensure_pool():
    """Return the pool, creating it on first use or after a reset."""
    global db_pool
    if db_pool is None:
        with _pool_lock:
            if db_pool is None:
                db_pool = psycopg2.pool.SimpleConnectionPool(1, 20, DATABASE_URL)
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


@app.route('/admin/login', methods=['GET', 'POST'])
def admin_login_post():
    """Handle login. GET just sends you to the login page - the form posts here,
    but people (and browsers restoring tabs) hit the URL directly too."""
    if request.method == 'GET':
        return redirect('/admin')

    password = request.form.get('password') or ''
    expected = os.getenv('ADMIN_PASSWORD') or ''

    if expected and hmac.compare_digest(password, expected):
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
        categories=CATEGORY_LABELS,
        statuses=LISTING_STATUSES,
        storage_ok=storage_configured(),
    )


@app.route('/admin/listings/new', methods=['GET', 'POST'])
@login_required
def admin_listing_new():
    """Create a listing, uploading any attached photos to Supabase Storage."""
    if request.method == 'GET':
        return render_template(
            'admin_listing_form.html',
            categories=CATEGORY_LABELS,
            item_types=ITEM_TYPES,
            storage_ok=storage_configured(),
            item=None,
        )

    title = (request.form.get('title') or '').strip()
    category = (request.form.get('category') or '').strip()
    item_type = (request.form.get('item_type') or 'console').strip()
    if item_type not in ITEM_TYPES:
        item_type = 'console'
    description = (request.form.get('description') or '').strip()
    price_raw = (request.form.get('price') or '').strip()
    stock_raw = (request.form.get('stock') or '0').strip()

    errors = []
    if not title:
        errors.append('Title is required.')
    if not category:
        errors.append('Category is required.')
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
            categories=CATEGORY_LABELS,
            item_types=ITEM_TYPES,
            storage_ok=storage_configured(),
            errors=errors,
            form=request.form,
            item=None,
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

    # Photos: uploads first, then any pasted URLs.
    images = []
    uploads = [f for f in request.files.getlist('photos') if f and f.filename]
    for i, upload in enumerate(uploads):
        if upload.mimetype not in ALLOWED_UPLOAD_TYPES:
            print(f"⚠️ Skipped {upload.filename}: unsupported type {upload.mimetype}")
            continue
        public_url = upload_listing_image(upload, slug, i)
        if public_url:
            images.append(public_url)

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
ALLOWED_UPLOAD_TYPES = {'image/jpeg', 'image/png', 'image/webp', 'image/gif'}


def storage_configured():
    return bool(SUPABASE_URL and SUPABASE_SERVICE_KEY)


def process_image(file_storage):
    """Downscale to MAX_IMAGE_EDGE and re-encode as WebP. Returns bytes."""
    from PIL import Image, ImageOps
    import io

    image = Image.open(file_storage.stream)
    image = ImageOps.exif_transpose(image)      # honour phone orientation
    if image.mode not in ('RGB', 'RGBA'):
        image = image.convert('RGB')

    image.thumbnail((MAX_IMAGE_EDGE, MAX_IMAGE_EDGE), Image.LANCZOS)

    buffer = io.BytesIO()
    image.save(buffer, format='WEBP', quality=WEBP_QUALITY, method=6)
    return buffer.getvalue()


def upload_listing_image(file_storage, slug, index):
    """Upload one processed image. Returns its public URL, or None on failure."""
    if not storage_configured():
        print("⚠️ Upload skipped: SUPABASE_URL / SUPABASE_SERVICE_KEY not set")
        return None

    try:
        data = process_image(file_storage)
    except Exception as e:
        print(f"⚠️ Could not process image: {e}")
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


def slugify(value):
    """Turn a title into a URL-safe slug: 'Rayquaza Edition SP' -> 'rayquaza-edition-sp'."""
    value = unicodedata.normalize('NFKD', str(value))
    value = value.encode('ascii', 'ignore').decode('ascii')
    value = re.sub(r'[^\w\s-]', '', value).strip().lower()
    value = re.sub(r'[-\s]+', '-', value)
    return value or 'item'


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


def normalize_image(path):
    """Stored paths look like 'static/images/x.png'. Make them root-relative."""
    if not path:
        return ''
    path = str(path)
    if path.startswith(('http://', 'https://', '/')):
        return path
    return '/' + path.lstrip('/')


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
            'description': description or '',
            'images': [normalize_image(p) for p in images_list],
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

        backfill_slugs()
        print("✅ Database tables initialized")
    except Exception as e:
        # A sleeping database at boot must not take the whole app down - the pool
        # rebuilds itself on the first request that gets through.
        print(f"⚠️ Skipping database init ({e}); will retry on first request")


# Initialize database on startup
init_db()


@app.route('/')
def index():
    return render_template('index.html', stripe_key=STRIPE_PUBLISHABLE_KEY)


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

        root = request.url_root.rstrip('/')
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
            'success_url': request.url_root + 'success?session_id={CHECKOUT_SESSION_ID}',
            # The token rides along so backing out of Stripe frees the stock
            # immediately instead of waiting for the hold to time out.
            'cancel_url': request.url_root + 'cancel?hold=' + hold_token,
            'expires_at': int(datetime.now().timestamp()) + HOLD_MINUTES * 60,
            # The hold token is all the webhook needs - the reservation rows
            # already record exactly which listings to decrement.
            'metadata': {'hold_token': hold_token},
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

CATEGORY_LABELS = {
    'gameboy-color': 'Game Boy Color',
    'gameboy-advance': 'Game Boy Advance',
    'gameboy-advance-sp': 'Game Boy Advance SP',
    'nintendo-ds-lite': 'Nintendo DS Lite',
    'mod-services': 'Mod & Repair Services',
    'lessons': 'Modding Lessons',
}

ITEM_TYPES = {
    'console': 'Console (physical, finite stock)',
    'service': 'Service (send-in repair or mod)',
    'lesson': 'Lesson (booked time)',
}


def category_label(slug):
    return CATEGORY_LABELS.get(slug, slug.replace('-', ' ').title())


@app.route('/shop')
def shop_index():
    """Crawlable grid of every listing, sold ones included."""
    try:
        items = fetch_listings()
    except Exception as e:
        print(f"Error loading shop index: {e}")
        items = []

    grouped = {}
    for item in items:
        grouped.setdefault(item['category'], []).append(item)

    # Note: not named 'items' - Jinja would resolve category.items to the dict's
    # built-in method instead of this key.
    categories = [
        {'slug': slug, 'label': category_label(slug), 'products': group}
        for slug, group in sorted(grouped.items())
    ]

    return render_template(
        'shop.html',
        categories=categories,
        total=len(items),
        canonical=request.url_root.rstrip('/') + '/shop',
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

    root = request.url_root.rstrip('/')
    canonical = f"{root}/listing/{item['slug']}"
    images_abs = [root + p if p.startswith('/') else p for p in item['images']]

    # Schema.org Product - this is what produces price and availability in
    # Google results rather than a plain blue link.
    structured_data = {
        '@context': 'https://schema.org',
        '@type': 'Product',
        'name': item['title'],
        'description': item['description'],
        'image': images_abs,
        'category': category_label(item['category']),
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
        category_name=category_label(item['category']),
        canonical=canonical,
        og_image=images_abs[0] if images_abs else '',
        structured_data=json.dumps(structured_data, indent=2),
        related=related,
    )


@app.context_processor
def inject_year():
    """Makes {{ year }} available to every template (used in the footer)."""
    return {'year': datetime.now().year}


@app.route('/robots.txt')
def robots_txt():
    root = request.url_root.rstrip('/')
    body = '\n'.join([
        'User-agent: *',
        'Allow: /',
        'Disallow: /admin',
        'Disallow: /api/',
        '',
        f'Sitemap: {root}/sitemap.xml',
        '',
    ])
    return Response(body, mimetype='text/plain')


@app.route('/sitemap.xml')
def sitemap_xml():
    """Every URL worth indexing. Google reads this instead of guessing."""
    root = request.url_root.rstrip('/')
    try:
        items = fetch_listings()
    except Exception as e:
        print(f"Error building sitemap: {e}")
        items = []

    urls = [
        {'loc': f'{root}/', 'priority': '1.0', 'changefreq': 'weekly'},
        {'loc': f'{root}/shop', 'priority': '0.9', 'changefreq': 'daily'},
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

            hold_token = (session.get('metadata') or {}).get('hold_token')

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
                        INSERT INTO orders (session_id, customer_email, customer_name, amount, shipping_address, items)
                        VALUES (%s, %s, %s, %s, %s, %s)
                        ON CONFLICT (session_id) DO NOTHING
                    ''', (
                        session_id,
                        customer_email,
                        shipping_name,
                        amount_total,
                        json.dumps(shipping_address),
                        json.dumps(purchased)
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
    return render_template('index.html', stripe_key=STRIPE_PUBLISHABLE_KEY), 404


if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)