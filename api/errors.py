from flask import jsonify


def success_response(data, meta=None, status_code=200):
    """Standard success envelope."""
    body = {"data": data}
    if meta:
        body["meta"] = meta
    return jsonify(body), status_code


def error_response(message, code, status_code):
    """Standard error envelope."""
    return jsonify({"error": message, "code": code}), status_code


def not_found(message="Resource not found"):
    return error_response(message, "NOT_FOUND", 404)


def bad_request(message="Invalid request"):
    return error_response(message, "BAD_REQUEST", 400)


def validation_error(message):
    return error_response(message, "VALIDATION_ERROR", 422)


def server_error(message="Internal server error"):
    return error_response(message, "SERVER_ERROR", 500)
