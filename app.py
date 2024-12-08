import os
import sqlite3

from dotenv import load_dotenv
from flask import Flask, render_template, request, redirect, url_for, jsonify, session, flash
import secrets
from services.buz_data import get_open_orders, get_schedule_jobs_details
from authlib.integrations.flask_client import OAuth
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required
from datetime import timedelta
from services.update_status_mapping import get_status_mapping, get_status_mappings, edit_status_mapping, \
    populate_status_mapping_table
import logging


logging.basicConfig(level=logging.DEBUG)
permanent_session_lifetime = timedelta(minutes=30)

# Load environment variables from .env
load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET")

# Initialize OAuth and LoginManager
oauth = OAuth(app)
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = "login" # Set the default login view

# Customize messages (optional)
login_manager.login_message = "Please log in to access this page."
login_manager.login_message_category = "error"


# Handle unauthorized access by redirecting to login
@login_manager.unauthorized_handler
def handle_unauthorized():
    app.logger.warning("Unauthorized access attempt.")
    return redirect(url_for("login"))


google = oauth.register(
    name="google",
    client_id=os.getenv("GOOGLE_CLIENT_ID"),
    client_secret=os.getenv("GOOGLE_CLIENT_SECRET"),
    access_token_url="https://oauth2.googleapis.com/token",
    authorize_url="https://accounts.google.com/o/oauth2/auth",
    api_base_url="https://www.googleapis.com/oauth2/v1/",
    userinfo_endpoint="https://openidconnect.googleapis.com/v1/userinfo",
    jwks_uri="https://www.googleapis.com/oauth2/v3/certs",
    client_kwargs={
        "scope": "openid email profile",
        "token_endpoint_auth_method": "client_secret_post",
        "prompt": "consent"
    }
)


# Mock User class for simplicity
class User(UserMixin):
    def __init__(self, id_, name, email):
        self.id = id_
        self.name = name
        self.email = email


users = {}  # A simple in-memory user store


@login_manager.user_loader
def load_user(user_id):
    return users.get(user_id)


@app.route("/login")
def login():
    return google.authorize_redirect(url_for("callback", _external=True))


# Load allowed users from environment
ALLOWED_USERS = set(os.getenv("ALLOWED_USERS", "").split(","))


@app.route("/callback")
def callback():
    token = google.authorize_access_token()
    print("Token:", token)  # Debugging
    user_info = google.get("userinfo").json()
    print("User Info:", user_info)  # Debugging

    # Use email as the unique user identifier
    user_email = user_info.get("email")
    if not user_email or user_email not in ALLOWED_USERS:
        return render_template('403.html'), 403

    session.permanent = True

    # Use email as the user_id and proceed with login
    user_id = user_email
    user_name = user_info.get("name", "Unknown")  # Optional, for display purposes

    # Create the user and log them in
    user = User(id_=user_id, name=user_name, email=user_email)
    users[user_id] = user
    login_user(user)

    return redirect(url_for("admin"))


@app.route("/logout")
@login_required
def logout():
    logout_user()
    return render_template('home.html')


# Database file path
DB_PATH = os.getenv("DATABASE_PATH", "customers.db")


# Function to initialize the database
def create_customers_table():
    try:
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        cur.execute('''
            CREATE TABLE IF NOT EXISTS customers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                dd_name TEXT,
                cbr_name TEXT,
                obfuscated_id TEXT NOT NULL UNIQUE
            )
        ''')
        conn.commit()
        conn.close()
        print("Database and table created successfully")
    except sqlite3.Error as e:
        print(f"Database initialization error: {e}")


# Helper function to interact with the database
def query_db(query, args=(), one=False):
    try:
        with sqlite3.connect(DB_PATH) as conn:
            cur = conn.cursor()
            cur.execute(query, args)
            rv = cur.fetchall()
            conn.commit()
            return (rv[0] if rv else None) if one else rv
    except sqlite3.Error as e:
        print(f"Database error: {e}")
        return None


@app.route('/')
def home():
    return render_template('home.html')


@app.route('/admin', methods=['GET', 'POST'])
@login_required
def admin():
    create_customers_table()
    if request.method == 'POST':
        dd_name = request.form['dd_name']
        cbr_name = request.form['cbr_name']
        obfuscated_id = secrets.token_urlsafe(8)

        # Insert customer into the database
        query_db(
            "INSERT INTO customers (dd_name, cbr_name, obfuscated_id) VALUES (?, ?, ?)",
            (dd_name, cbr_name, obfuscated_id),
        )
        return redirect(url_for('admin'))

    # Retrieve all customers from the database
    customers = query_db("SELECT id, dd_name, cbr_name, obfuscated_id FROM customers")
    if customers:
        return render_template('admin.html', customers=customers)
    return render_template('admin.html')


@app.route('/etas/<obfuscated_id>')
def show_report(obfuscated_id):
    customer = query_db("SELECT dd_name, cbr_name FROM customers WHERE obfuscated_id = ?", (obfuscated_id,), one=True)
    if not customer:
        error_message = f"No report found for ID: {obfuscated_id}"
        return render_template('404.html', message=error_message), 404

    try:
        # Fetch data for both instances
        conn = sqlite3.connect(DB_PATH)
        data_dd = get_open_orders(conn, customer[0], "DD")
        data_cbr = get_open_orders(conn, customer[1], "CBR")
        conn.close()

        # Combine the results
        combined_data = data_cbr + data_dd
        return render_template('report.html', data=combined_data)
    except Exception as e:
        error_message = f"Failed to generate report for ID: {obfuscated_id}. Error: {str(e)}"
        return render_template('500.html', message=error_message), 500


@app.route('/delete/<int:customer_id>')
@login_required
def delete_customer(customer_id):
    query_db("DELETE FROM customers WHERE id = ?", (customer_id,))
    return redirect(url_for('admin'))


@app.route('/edit/<int:customer_id>', methods=['GET', 'POST'])
@login_required
def edit_customer(customer_id):
    if request.method == 'POST':
        # Fetch updated form data
        dd_name = request.form['dd_name']
        cbr_name = request.form['cbr_name']

        # Update the customer in the database
        query_db(
            "UPDATE customers SET cbr_name = ?, dd_name = ? WHERE id = ?",
            (cbr_name, dd_name, customer_id),
        )
        return redirect(url_for('admin'))

    # Fetch customer details for pre-filling the form
    customer = query_db(
        "SELECT id, dd_name, cbr_name FROM customers WHERE id = ?", (customer_id,), one=True
    )
    if not customer:
        return "Customer not found", 404

    return render_template('edit.html', customer=customer)


@app.route('/jobs-schedule/<order_no>', methods=['GET'])
@login_required
def jobs_schedule(order_no):
    """Route to fetch JobsScheduleDetails for a given order number."""
    data = get_schedule_jobs_details(order_no, "JobsScheduleDetailed", "DD")
    return jsonify(data)


@app.route('/wip/<order_no>', methods=['GET'])
@login_required
def work_in_progress(order_no):
    """Route to fetch WorkInProgress for a given order number."""
    data = get_schedule_jobs_details(order_no, "WorkInProgress")
    return jsonify(data)


@app.route('/status_mapping')
@login_required
def list_status_mappings():
    conn = sqlite3.connect(DB_PATH)
    mappings = get_status_mappings(conn)
    conn.close()
    return render_template('status_mappings.html', mappings=mappings)


# Error Handlers
@app.route('/status_mapping/edit/<int:mapping_id>', methods=['GET', 'POST'])
def edit_status_mapping_route(mapping_id):
    conn = sqlite3.connect(DB_PATH)

    if request.method == 'POST':
        custom_status = request.form['custom_status']
        active = 'active' in request.form
        edit_status_mapping(conn, mapping_id, custom_status, active)
        conn.close()
        return redirect(url_for('list_status_mappings'))

    mapping = get_status_mapping(conn, mapping_id)
    conn.close()
    return render_template('edit_status_mapping.html', mapping=mapping)


@app.route('/refresh_statuses', methods=['POST'])
def refresh_statuses():
    try:
        # Call the function to refresh statuses
        conn = sqlite3.connect(DB_PATH)
        populate_status_mapping_table(conn)
        conn.close()
        flash("Statuses refreshed successfully.", "success")
    except Exception as e:
        flash(f"Failed to refresh statuses: {e}", "danger")

    # Redirect back to the edit page
    return redirect(url_for('list_status_mappings'))


@app.errorhandler(401)
def forbidden(error):
    return render_template('401.html'), 401


@app.errorhandler(403)
def forbidden(error):
    return render_template('403.html'), 403


@app.errorhandler(404)
def page_not_found(error):
    return render_template('404.html'), 404


@app.errorhandler(405)
def page_not_found(error):
    return render_template('405.html'), 405


@app.errorhandler(429)
def page_not_found(error):
    return render_template('429.html'), 429


@app.errorhandler(500)
def internal_server_error(error):
    return render_template('500.html'), 500


@app.route('/500')
def error500():
    return 1/0


if __name__ == '__main__':
    app.run()
