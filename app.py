import os
from services.database import get_db, query_db, execute_query
from werkzeug.exceptions import HTTPException
from dotenv import load_dotenv
from flask import Flask, render_template, request, redirect, url_for, jsonify, flash, g, send_from_directory
from flask_login import current_user
import secrets
from services.buz_data import get_open_orders
from authlib.integrations.flask_client import OAuth
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required
from datetime import timedelta
from services.update_status_mapping import get_status_mapping, get_status_mappings, edit_status_mapping, \
    populate_status_mapping_table
import logging.config
from functools import wraps
from flask import abort
from flask_wtf.csrf import CSRFProtect
import sentry_sdk
from sentry_sdk.integrations.flask import FlaskIntegration
from services.database import create_db_tables
from datetime import datetime


# Load environment variables from .env
load_dotenv()

# Initialize Sentry
sentry_sdk.init(
    dsn=os.getenv("SENTRY_DSN"),  # Replace this with your DSN URL or use an environment variable
    integrations=[FlaskIntegration()],
    traces_sample_rate=1.0,  # Adjust sampling rate if needed
    environment=os.getenv("FLASK_ENV", "development"),  # Set to "production" in production
    send_default_pii=True  # Enable personal identifiable information for better logs
)

# Initialize Flask app
app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET")

# Set app configurations
app.permanent_session_lifetime = timedelta(minutes=30)

csrf = CSRFProtect(app)

# Set up logging
LOGGING_CONFIG = {
    'version': 1,
    'formatters': {
        'default': {
            'format': '[%(asctime)s] %(levelname)s in %(module)s: %(message)s',
        },
    },
    'handlers': {
        'console': {
            'class': 'logging.StreamHandler',
            'formatter': 'default',
        },
    },
    'root': {
        'level': 'INFO',
        'handlers': ['console'],
    },
}

if os.getenv("ENV") == "production":
    app.config['SESSION_COOKIE_SECURE'] = True  # Use HTTPS (enable in production)
    app.config['SESSION_COOKIE_HTTPONLY'] = True  # Prevent JavaScript access to cookies
    app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'  # Mitigate CSRF risks
    app.config['REMEMBER_COOKIE_DURATION'] = timedelta(minutes=60)  # Session length


logging.config.dictConfig(LOGGING_CONFIG)

# Initialize OAuth and LoginManager
oauth = OAuth(app)
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = "login" # Set the default login view

# Customize messages (optional)
login_manager.login_message = "Please log in to access this page."
login_manager.login_message_category = "error"


# Close the database connection when the app context is destroyed
@app.teardown_appcontext
def close_db(exception):
    db = g.pop('db', None)
    if db:
        db.close()


# Handle unauthorized access by redirecting to login
@login_manager.unauthorized_handler
def handle_unauthorized():
    app.logger.warning("Unauthorized access attempt.")
    return redirect(url_for("login"))


@app.cli.command("init-db")
def initialize_database():
    """Initialize the database tables."""
    create_db_tables()
    print("Database initialized.")


# Initialize the database when the app starts
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
    def __init__(self, id_, name, email, role):
        self.id = id_
        self.name = name
        self.email = email
        self.role = role


@app.route("/login")
def login():
    return google.authorize_redirect(url_for("callback", _external=True))


def role_required(*required_roles):
    def decorator(f):
        @wraps(f)
        def wrapped(*args, **kwargs):
            if not current_user.is_authenticated or current_user.role not in required_roles:
                abort(403)  # Forbidden access
            return f(*args, **kwargs)
        return wrapped
    return decorator


@app.route("/callback")
def callback():
    token = google.authorize_access_token()

    if not token or token.get('expires_in', 0) <= 0:
        print("Token expired or missing, redirecting to login.")
        return redirect(url_for("login"))

    user_info = google.get("userinfo").json()
    user_email = user_info.get("email")

    if not user_email:
        flash("Login failed: No email found.", "error")
        return redirect(url_for("login"))

    # Query database for user
    user_data = query_db(
        "SELECT id, email, name, role, active FROM users WHERE email = ?",
        (user_email,), one=True, logger=app.logger
    )

    if not user_data or not user_data[4]:  # Check if user exists and is active
        flash("Access denied: Unauthorized user.", "error")
        return render_template('403.html'), 403

    # Log the user in
    user = User(id_=user_data[0], name=user_data[2], email=user_data[1], role=user_data[3])
    login_user(user)

    return redirect(url_for("admin"))


@app.route("/logout")
@login_required
def logout():
    logout_user()
    return render_template('home.html')


@app.route('/')
def home():
    return render_template('home.html')


@app.route('/admin', methods=['GET', 'POST'])
@login_required
@role_required('admin', 'user')  # Allow both roles
def admin():
    if request.method == 'POST':
        dd_name = request.form['dd_name']
        cbr_name = request.form['cbr_name']
        obfuscated_id = secrets.token_urlsafe(30)
        print(f"Generated Token: {obfuscated_id}")
        print(f"Length of Token: {len(obfuscated_id)}")

        # Insert customer into the database
        query_db(
            "INSERT INTO customers (dd_name, cbr_name, obfuscated_id) VALUES (?, ?, ?)",
            (dd_name, cbr_name, obfuscated_id), logger=app.logger
        )
        return redirect(url_for('admin'))

    # Retrieve all customers from the database
    customers = query_db("SELECT id, dd_name, cbr_name, obfuscated_id FROM customers")
    # Sort using the combined name logic
    sorted_customers = sorted(
        customers,
        key=lambda c: (c[1] or c[2] or "").lower()
    )
    if customers:
        return render_template('admin.html', customers=sorted_customers)
    return render_template('admin.html')


@app.route('/etas/<code>')
def eta_report_redirect(code):
    # Redirect to the new URL format
    return redirect(url_for('eta_report', obfuscated_id=code), code=301)


@app.route('/<obfuscated_id>')
def eta_report(obfuscated_id):
    customer = query_db("SELECT dd_name, cbr_name FROM customers WHERE obfuscated_id = ?", (obfuscated_id,), one=True)

    if not customer:
        error_message = f"No report found for ID: {obfuscated_id}"
        return render_template('404.html', message=error_message), 404

    try:
        # Fetch data for both instances
        data_dd = get_open_orders(get_db(), customer[0], "DD")
        data_cbr = get_open_orders(get_db(), customer[1], "CBR")

        # Combine the results
        combined_data = data_cbr + data_dd

        # Group by RefNo
        grouped_data = []
        refno_to_date = {}

        for item in combined_data:
            ref_no = item.get("RefNo")
            date_str = item.get("DateScheduled", "N/A")

            if ref_no not in refno_to_date:
                try:
                    refno_to_date[ref_no] = datetime.strptime(date_str,
                                                              "%d %b %Y") if date_str != "N/A" else datetime.min
                except ValueError:
                    refno_to_date[ref_no] = datetime.min  # Fallback for invalid date

            # Add item to the group
            group_entry = next((entry for entry in grouped_data if entry["RefNo"] == ref_no), None)
            if not group_entry:
                group_entry = {"RefNo": ref_no, "group_items": [], "DateScheduled": date_str}
                grouped_data.append(group_entry)
            group_entry["group_items"].append(item)

            # Sort groups by DateScheduled
        grouped_data.sort(key=lambda g: refno_to_date.get(g["RefNo"], datetime.min))

        # Prepare customer name: handle the cases based on the conditions
        if customer[0] == customer[1] or customer[1] == '':
            customer_name = customer[0]  # Use customer[0] if they are the same or if customer[1] is empty
        elif customer[0] == '':
            customer_name = customer[1]  # Use customer[1] if customer[0] is empty
        else:
            customer_name = f"{customer[0]} / {customer[1]}"  # Use both names if they are different

        # Compute unique filter options from combined data
        unique_statuses = sorted({item.get("ProductionStatus", "N/A") for item in combined_data})
        unique_groups = sorted({item.get("ProductionLine", "N/A") for item in combined_data})
        unique_suppliers = sorted({item.get("Instance", "N/A") for item in combined_data})

        # Pass the customer name along with the data to the template
        if combined_data:
            return render_template(
                'report.html',
                data=grouped_data,
                customer_name=customer_name,
                statuses=unique_statuses,
                groups=unique_groups,
                suppliers=unique_suppliers
            )

        return render_template('report.html', customer_name=customer_name)

    except Exception as e:
        if app.debug:
            raise e
        else:
            error_message = f"Failed to generate report for ID: {obfuscated_id}. Error: {str(e)}"
            return render_template('500.html', message=error_message), 500


@app.route('/delete/<int:customer_id>')
@login_required
@role_required('admin', 'user')  # Allow both roles
def delete_customer(customer_id):
    query_db("DELETE FROM customers WHERE id = ?", (customer_id,))
    return redirect(url_for('admin'))


@app.route('/edit/<int:customer_id>', methods=['GET', 'POST'])
@login_required
@role_required('admin', 'user')  # Allow both roles
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
    try:
        # Call the function to get the data
        data = get_data_by_order_no(order_no, "JobsScheduleDetailed", "DD")
        return jsonify(data)

    except requests.exceptions.RequestException as e:
        # Handle the error based on the environment
        if app.debug:
            # In non-production environments, raise the exception for debugging
            raise e
        else:
            return jsonify({"error": f"Failed to fetch JobsScheduleDetails: {str(e)}"}), 500


@app.route('/wip/<order_no>', methods=['GET'])
@login_required
def work_in_progress(order_no):
    """Route to fetch WorkInProgress for a given order number."""
    try:
        data = get_data_by_order_no(order_no, "WorkInProgress")
        return jsonify(data)

    except requests.exceptions.RequestException as e:
        # Handle the error based on the environment
        if app.debug:
            # In non-production environments, raise the exception for debugging
            raise e
        else:
            return jsonify({"error": f"Failed to fetch WorkInProgress: {str(e)}"}), 500


@app.route('/status_mapping')
@login_required
@role_required('admin', 'user')  # Allow both roles
def list_status_mappings():
    mappings = get_status_mappings(conn=get_db())
    return render_template('status_mappings.html', mappings=mappings)


# Error Handlers
@app.route('/status_mapping/edit/<int:mapping_id>', methods=['GET', 'POST'])
@role_required('admin', 'user')  # Allow both roles
def edit_status_mapping_route(mapping_id):
    if request.method == 'POST':
        custom_status = request.form['custom_status']
        active = 'active' in request.form
        edit_status_mapping(mapping_id, custom_status, active, conn=get_db())
        return redirect(url_for('list_status_mappings'))

    mapping = get_status_mapping(mapping_id=mapping_id, conn=get_db())
    return render_template('edit_status_mapping.html', mapping=mapping)


@app.route('/refresh_statuses', methods=['POST'])
@role_required('admin')  # Allow both roles
def refresh_statuses():
    try:
        # Call the function to refresh statuses
        populate_status_mapping_table(get_db())
        flash("Statuses refreshed successfully.", "success")
    except Exception as e:
        if app.debug:
            raise e
        else:
            flash(f"Failed to refresh statuses: {e}", "danger")

    # Redirect back to the edit page
    return redirect(url_for('list_status_mappings'))


@app.errorhandler(HTTPException)
def handle_http_exception(e):
    """Handle specific HTTP errors with custom pages or fall back to a generic page."""
    app.logger.error(f"HTTP Error {e.code}: {e.description}")

    # Optionally log this error to Sentry or another monitoring tool
    sentry_sdk.capture_exception(e)

    # Check if a specific error page exists for the given code
    if e.code in {401, 403, 404, 405, 429, 500}:
        template_name = f"{e.code}.html"
    else:
        template_name = "error.html"

    if app.debug:
        raise e
    else:
        # Render the template with the provided error details
        return render_template(template_name, code=e.code, message=e.description), e.code


@app.errorhandler(Exception)
def handle_exception(e):
    """Handle unexpected errors like database failures or crashes."""
    app.logger.error(f"Unexpected Server Error: {e}")

    # Optionally log this error to Sentry or another monitoring tool
    sentry_sdk.capture_exception(e)

    if app.debug:
        raise e
    else:
        # Fall back to a generic error page with a 500 status code
        return render_template(
            "error.html",
            code=500,
            message=str(e) or "An unexpected server error occurred."
        ), 500


@app.route('/manage_users')
@login_required
@role_required('admin')
def manage_users():
    users = query_db("SELECT id, email, name, role, active FROM users")
    return render_template('manage_users.html', users=users)


@app.route('/add_user', methods=['POST'])
@login_required
@role_required('admin')
def add_user():
    email = request.form['email']
    name = request.form['name']
    role = request.form['role']

    try:
        execute_query(
            "INSERT INTO users (email, name, role) VALUES (?, ?, ?)",
            (email, name, role)
        )
        flash("User added successfully.", "success")
    except ValueError:
        flash("Email already exists.", "danger")

    return redirect(url_for('manage_users'))


@app.route('/edit_user/<int:user_id>', methods=['GET', 'POST'])
@login_required
@role_required('admin')
def edit_user(user_id):
    if request.method == 'POST':
        email = request.form['email']
        name = request.form['name']
        role = request.form['role']

        query_db(
            "UPDATE users SET email = ?, name = ?, role = ? WHERE id = ?",
            (email, name, role, user_id)
        )

        flash("User updated successfully.", "success")
        return redirect(url_for('manage_users'))

    # Pre-fill form for editing
    user = query_db("SELECT id, email, name, role FROM users WHERE id = ?", (user_id,), one=True)

    if user:
        user = dict(user)
    else:
        flash("User not found.", "danger")
        return redirect(url_for('manage_users'))

    return render_template('edit_user.html', user=user)


@app.route('/toggle_user_status/<int:user_id>')
@login_required
@role_required('admin')
def toggle_user_status(user_id):
    user = query_db("SELECT active FROM users WHERE id = ?", (user_id,), one=True)

    if not user:
        flash("User not found.", "danger")
        return redirect(url_for('manage_users'))

    new_status = 0 if user[0] == 1 else 1
    query_db("UPDATE users SET active = ? WHERE id = ?", (new_status, user_id))

    flash("User status updated successfully.", "success")
    return redirect(url_for('manage_users'))


@app.route('/delete_user/<int:user_id>')
@login_required
@role_required('admin')
def delete_user(user_id):
    query_db("DELETE FROM users WHERE id = ?", (user_id,))

    flash("User deleted successfully.", "success")
    return redirect(url_for('manage_users'))


@login_manager.user_loader
def load_user(user_id):
    user_data = query_db("SELECT id, email, name, role, active FROM users WHERE id = ?", (user_id,), one=True)

    if user_data and user_data[4]:  # Ensure user exists and is active
        return User(id_=user_data[0], name=user_data[2], email=user_data[1], role=user_data[3])
    return None


@app.route('/sentry-debug')
def trigger_error():
    division_by_zero = 1 / 0


# Route for favicon.ico
@app.route('/favicon.ico')
def favicon():
    return send_from_directory(
        app.static_folder, 'favicon.ico', mimetype='image/vnd.microsoft.icon'
    )


@app.route('/robots.txt')
def robots_txt():
    return send_from_directory(app.static_folder, 'robots.txt')


# Required Environment Variables
REQUIRED_ENV_VARS = [
    "BUZ_DD_USERNAME", "BUZ_DD_PASSWORD",
    "BUZ_CBR_USERNAME", "BUZ_CBR_PASSWORD",
    "FLASK_SECRET", "GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET",
    "GOOGLE_REDIRECT_URI", "DATABASE_PATH", "SERVER_NAME", "SENTRY_DSN"
]

# Validate Environment Variables
missing_vars = [var for var in REQUIRED_ENV_VARS if not os.getenv(var)]

if missing_vars:
    raise RuntimeError(f"Missing required environment variables: {', '.join(missing_vars)}")


if __name__ == '__main__':
    app.run()
