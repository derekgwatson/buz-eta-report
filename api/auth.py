import hmac
import os
from functools import wraps

from flask import current_app, request

from api.errors import error_response


def api_key_required(f):
    """Require a valid API key in the X-API-Key header."""

    @wraps(f)
    def decorated(*args, **kwargs):
        api_key = os.environ.get("BUZ_API_KEY")
        if not api_key:
            current_app.logger.error("BUZ_API_KEY not configured")
            return error_response(
                "API key not configured on server",
                "SERVER_CONFIG_ERROR",
                500,
            )

        provided_key = request.headers.get("X-API-Key")
        if not provided_key:
            return error_response("Missing X-API-Key header", "UNAUTHORIZED", 401)

        if not hmac.compare_digest(provided_key.encode(), api_key.encode()):
            return error_response("Invalid API key", "FORBIDDEN", 403)

        return f(*args, **kwargs)

    return decorated
