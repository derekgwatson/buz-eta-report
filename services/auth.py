import os
from flask import redirect, url_for, render_template, flash
from flask_login import LoginManager, UserMixin, login_user
from authlib.integrations.flask_client import OAuth

# Initialize Flask-Login and OAuth
login_manager = LoginManager()
oauth = OAuth()

# User storage
users = {}

# Environment Variable Check
SKIP_GOOGLE_AUTH = os.getenv("SKIP_GOOGLE_AUTH", "False") == "True"

# Load allowed users from environment
ALLOWED_USERS = set(os.getenv("ALLOWED_USERS", "").split(","))


# Define the Google OAuth service
if not SKIP_GOOGLE_AUTH:
    google = oauth.register(
        name="google",
        client_id=os.getenv("GOOGLE_CLIENT_ID"),
        client_secret=os.getenv("GOOGLE_CLIENT_SECRET"),
        access_token_url="https://oauth2.googleapis.com/token",
        authorize_url="https://accounts.google.com/o/oauth2/auth",
        api_base_url="https://www.googleapis.com/auth/userinfo.email",
        userinfo_endpoint="https://openidconnect.googleapis.com/v1/userinfo",
        client_kwargs={
            "scope": "openid email profile",
            "token_endpoint_auth_method": "client_secret_post",
            "prompt": "consent"
        }
    )


# Mock User Class
class MockUser(UserMixin):
    def __init__(self, id_, name, email):
        self.id = id_
        self.name = name
        self.email = email


# Load User Function
@login_manager.user_loader
def load_user(user_id):
    return users.get(user_id)


# Define Auth Routes
def setup_auth_routes(app):
    if not SKIP_GOOGLE_AUTH:
        @app.route("/login")
        def login():
            return google.authorize_redirect(url_for("callback", _external=True))

        @app.route("/callback")
        def callback():
            token = google.authorize_access_token()
            user_info = google.get("userinfo").json()

            user_email = user_info.get("email")
            if not user_email or user_email not in os.getenv("ALLOWED_USERS", "").split(","):
                return render_template('403.html'), 403

            # Create or retrieve user
            user = MockUser(
                id_=user_email,
                name=user_info.get("name", "Unknown"),
                email=user_email
            )
            users[user_email] = user
            login_user(user)
            flash("Login successful!", "success")
            return redirect(url_for("admin"))

    else:
        print("Skipping Google Auth for Development")

        # Register mock user
        dev_user = MockUser(
            id_="dev@example.com",
            name="Dev User",
            email="dev@example.com"
        )
        users["dev@example.com"] = dev_user

        @app.route("/login")
        def login():
            login_user(dev_user)
            flash("Logged in as Mock User", "success")
            return redirect(url_for("admin"))

        @app.route("/callback")
        def callback():
            flash("Mock login successful!", "success")
            return redirect(url_for("admin"))
