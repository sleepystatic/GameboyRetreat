from flask import Flask, render_template, request, jsonify, redirect
import stripe
import os
import json
from datetime import datetime
from flask_mail import Mail, Message
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

# Email config
app.config['MAIL_SERVER'] = os.environ.get('MAIL_SERVER', 'smtp.mailgun.org')
app.config['MAIL_PORT'] = int(os.environ.get('MAIL_PORT', 587))
app.config['MAIL_USE_TLS'] = True
app.config['MAIL_USERNAME'] = os.environ.get('MAIL_USERNAME')
app.config['MAIL_PASSWORD'] = os.environ.get('MAIL_PASSWORD')
app.config['MAIL_DEFAULT_SENDER'] = os.environ.get('MAIL_DEFAULT_SENDER')

mail = Mail(app)

# PostgreSQL Database connection pool
try:
    db_pool = psycopg2.pool.SimpleConnectionPool(
        1, 20,
        os.environ.get('DATABASE_URL')
    )
    if db_pool:
        print("✅ Database connection pool created successfully")
except Exception as e:
    print(f"❌ Database connection failed: {e}")
    db_pool = None


@app.route('/admin')
def admin_login():
    """Show login page"""
    if current_user.is_authenticated:
        return redirect('/admin/dashboard')
    return render_template('admin_login.html')


@app.route('/admin/login', methods=['POST'])
def admin_login_post():
    """Handle login"""
    password = request.form.get('password')

    if password == os.getenv('ADMIN_PASSWORD'):
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
        conn = db_pool.getconn()
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM orders ORDER BY created_at DESC LIMIT 50')
        orders = cursor.fetchall()
        cursor.close()
        db_pool.putconn(conn)

        return render_template('admin_dashboard.html', orders=orders)
    except Exception as e:
        return f"Error: {e}", 500


@app.route('/admin/logout')
@login_required
def admin_logout():
    logout_user()
    return redirect('/admin')

# Database setup
def init_db():
    """Initialize the database with tables"""
    if not db_pool:
        print("⚠️ No database connection, skipping init")
        return

    conn = db_pool.getconn()
    cursor = conn.cursor()

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

    conn.commit()
    cursor.close()
    db_pool.putconn(conn)
    print("✅ Database tables initialized")


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
        conn = db_pool.getconn()
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO sellers (email, item, condition, price, shipping, timestamp)
            VALUES (%s, %s, %s, %s, %s, %s) RETURNING id
        ''', (email, item, condition, price, shipping, timestamp))
        submission_id = cursor.fetchone()[0]
        conn.commit()
        cursor.close()
        db_pool.putconn(conn)

        print(f"New seller submission #{submission_id}:")
        print(f"  Item: {item}")
        print(f"  Condition: {condition}")
        print(f"  Price: {price}")
        print(f"  Shipping: {shipping}")
        print(f"  Email: {email}")

        # Send email (non-blocking)
        try:
            msg = Message(
                subject=f'New Seller Submission: {item}',
                sender=app.config['MAIL_USERNAME'],
                recipients=[os.environ.get('MAIL_RECIPIENT')]
            )
            msg.body = f"""New submission:
Item: {item}
Condition: {condition}
Price: {price}
Shipping: {shipping}
Email: {email}
"""
            mail.send(msg)
            print("✅ Email sent")
        except Exception as email_err:
            print(f"⚠️ Email failed: {email_err}")

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
        conn = db_pool.getconn()
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM sellers ORDER BY created_at DESC')
        submissions = cursor.fetchall()
        cursor.close()
        db_pool.putconn(conn)

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

        # Convert cart items to Stripe line items
        line_items = []
        for item in cart_items:
            line_items.append({
                'price_data': {
                    'currency': 'usd',
                    'product_data': {
                        'name': item['title'],
                        'images': [request.url_root.rstrip('/') + '/' + item['img']],
                    },
                    'unit_amount': int(item['price'] * 100),
                },
                'quantity': 1,
            })

        # Create Stripe checkout session
        checkout_session = stripe.checkout.Session.create(
            payment_method_types=['card'],
            line_items=line_items,
            mode='payment',
            success_url=request.url_root + 'success?session_id={CHECKOUT_SESSION_ID}',
            cancel_url=request.url_root + 'cancel',
            shipping_address_collection={
                'allowed_countries': ['US', 'CA'],
            },
        )

        return jsonify({'sessionId': checkout_session.id})

    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/stock')
def get_stock():
    """Return current inventory from database"""
    try:
        conn = db_pool.getconn()
        cursor = conn.cursor()
        cursor.execute('''
            SELECT category, title, stock, price, description, images 
            FROM inventory 
            WHERE stock > 0
            ORDER BY category, title
        ''')
        items = cursor.fetchall()
        cursor.close()
        db_pool.putconn(conn)

        # Format as listings structure
        listings = {}
        for item in items:
            category, title, stock, price, description, images = item

            if category not in listings:
                listings[category] = []

            # Check if images is already a list (JSONB from PostgreSQL) or a string
            if isinstance(images, str):
                images_list = json.loads(images)
            elif isinstance(images, list):
                images_list = images  # Already a list from PostgreSQL JSONB
            else:
                images_list = []

            listings[category].append({
                'title': title,
                'description': description,
                'price': float(price),
                'stock': stock,
                'images': images_list
            })

        return jsonify(listings)

    except Exception as e:
        print(f"Error fetching stock: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500
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
    return render_template('cancel.html')


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

            # Save order to database
            try:
                conn = db_pool.getconn()
                cursor = conn.cursor()
                cursor.execute('''
                    INSERT INTO orders (session_id, customer_email, customer_name, amount, shipping_address, items)
                    VALUES (%s, %s, %s, %s, %s, %s)
                ''', (
                    session_id,
                    customer_email,
                    shipping_name,
                    amount_total,
                    json.dumps(shipping_address),
                    json.dumps(session.get('line_items', []))
                ))

                # Reduce stock in database
                for item in cart_items:
                    cursor.execute('''
                        UPDATE inventory 
                        SET stock = stock - 1 
                        WHERE category = %s AND title = %s
                    ''', (item['category'], item['title']))

                conn.commit()
                cursor.close()
                db_pool.putconn(conn)
                print(f"✅ Order saved: {session_id}")
            except Exception as db_err:
                print(f"⚠️ Order save failed: {db_err}")

            print(f"✅ Payment successful for: {customer_email} - ${amount_total}")

        return jsonify({'status': 'success'})

    except Exception as e:
        print(f"Webhook error: {str(e)}")
        return jsonify({'error': str(e)}), 400


@app.errorhandler(404)
def page_not_found(e):
    return render_template('index.html', stripe_key=STRIPE_PUBLISHABLE_KEY), 404


if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)