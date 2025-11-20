from flask import Flask, render_template, request, jsonify, redirect
import stripe
import os
import json
import sqlite3
from datetime import datetime
from flask_mail import Mail, Message
from dotenv import load_dotenv
import threading
import time
load_dotenv()

app = Flask(__name__)

# Stripe configuration
stripe.api_key = os.getenv('STRIPE_SECRET_KEY')
STRIPE_PUBLISHABLE_KEY = os.getenv('STRIPE_PUBLISHABLE_KEY')

# Email configuration - UPDATE THESE
ADMIN_EMAIL = os.getenv('ADMIN_EMAIL')  # ← UPDATE THIS with your email

# Email config
app.config['MAIL_SERVER'] = 'smtp.gmail.com'
app.config['MAIL_PORT'] = 587
app.config['MAIL_USE_TLS'] = True
app.config['MAIL_USERNAME'] = os.getenv('GMAIL_USER')
app.config['MAIL_PASSWORD'] = os.getenv('GMAIL_APP_PASSWORD')
app.config['MAIL_DEFAULT_SENDER'] = os.getenv('GMAIL_USER')
mail = Mail(app)


def send_email_async(app, msg, max_retries=3):
    """
    Send email asynchronously with retry logic and timeout handling.
    This prevents email sending from blocking the main application thread.
    """
    def _send_with_retry():
        retry_count = 0
        last_error = None

        while retry_count < max_retries:
            try:
                # Push app context for Flask-Mail
                with app.app_context():
                    # Set a timeout for the SMTP operation
                    mail.send(msg)
                    print(f"[EMAIL] Successfully sent email: {msg.subject}")
                    return
            except Exception as e:
                retry_count += 1
                last_error = str(e)
                print(f"[EMAIL] Attempt {retry_count}/{max_retries} failed: {last_error}")

                if retry_count < max_retries:
                    # Exponential backoff: 2s, 4s, 8s
                    backoff_time = 2 ** retry_count
                    print(f"[EMAIL] Retrying in {backoff_time} seconds...")
                    time.sleep(backoff_time)

        # All retries failed
        print(f"[EMAIL] Failed to send email after {max_retries} attempts: {last_error}")
        print(f"[EMAIL] Email subject: {msg.subject}")

    # Run email sending in a separate thread to avoid blocking
    thread = threading.Thread(target=_send_with_retry)
    thread.daemon = True  # Thread will not prevent app shutdown
    thread.start()
    print(f"[EMAIL] Queued email for async sending: {msg.subject}")


# Database setup
def init_db():
    """Initialize the database with sellers table"""
    conn = sqlite3.connect('submissions.db')
    c = conn.cursor()
    c.execute('''
        CREATE TABLE IF NOT EXISTS sellers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT,
            email TEXT NOT NULL,
            item TEXT NOT NULL,
            condition TEXT NOT NULL,
            price TEXT NOT NULL,
            shipping TEXT NOT NULL,
            timestamp TEXT NOT NULL,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    conn.commit()
    conn.close()


# Initialize database on startup
init_db()


@app.route('/')
def index():
    return render_template('index.html', stripe_key=STRIPE_PUBLISHABLE_KEY)


@app.route('/submit-seller', methods=['POST'])
# @limiter.limit("5 per hour")
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

        # Save to database
        conn = sqlite3.connect('submissions.db')
        c = conn.cursor()
        c.execute('''
            INSERT INTO sellers (email, item, condition, price, shipping, timestamp)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (email, item, condition, price, shipping, timestamp))
        conn.commit()
        submission_id = c.lastrowid
        conn.close()

        # Send email notification asynchronously
        print(f"New seller submission #{submission_id}:")
        print(f"  Item: {item}")
        print(f"  Condition: {condition}")
        print(f"  Price: {price}")
        print(f"  Shipping: {shipping}")
        print(f"  Email: {email}")
        print(f"  Timestamp: {timestamp}")

        # Prepare email message
        try:
            msg = Message(
                subject=f'New Seller Submission: {item}',
                sender=app.config['MAIL_USERNAME'],
                recipients=[ADMIN_EMAIL]
            )
            msg.body = f"""
New submission:
Item: {item}
Condition: {condition}
Price: {price}
Shipping: {shipping}
Email: {email}
"""
            # Send asynchronously to avoid blocking the response
            send_email_async(app, msg)
        except Exception as e:
            # Log email preparation errors but don't fail the request
            print(f"[EMAIL] Error preparing email: {str(e)}")
            # Continue processing - email failure shouldn't block submission

        return jsonify({
            'success': True,
            'message': 'Submission received!',
            'submission_id': submission_id
        })

    except Exception as e:
        print(f"Error in submit_seller: {str(e)}")
        return jsonify({'error': str(e)}), 500


@app.route('/view-submissions')
def view_submissions():
    """View all seller submissions - for admin use"""
    try:
        conn = sqlite3.connect('submissions.db')
        c = conn.cursor()
        c.execute('SELECT * FROM sellers ORDER BY created_at DESC')
        submissions = c.fetchall()
        conn.close()

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
                'created_at': sub[8]
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


@app.route('/success')
def success():
    session_id = request.args.get('session_id')

    if session_id:
        try:
            # Verify with Stripe that this session is real and paid
            session = stripe.checkout.Session.retrieve(session_id)
            if session.payment_status == "paid":
                return render_template('success.html', session_id=session_id)
            else:
                print(f"[STRIPE] Session {session_id} not paid: {session.payment_status}")
                return redirect('/')
        except Exception as e:
            print(f"[STRIPE] Error verifying session {session_id}: {str(e)}")
            # Still show success page even if verification fails
            # (user may have legitimate session but API call failed)
            return render_template('success.html', session_id=session_id)
    else:
        # No session ID provided, just show success page
        return render_template('success.html', session_id=session_id)


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
            print(f"Payment successful for session: {session['id']}")

        return jsonify({'status': 'success'})

    except Exception as e:
        return jsonify({'error': str(e)}), 400

@app.errorhandler(404)
def page_not_found(e):
        return render_template('index.html', stripe_key=STRIPE_PUBLISHABLE_KEY), 404

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)