from flask import Blueprint, jsonify


def create_api_bp() -> Blueprint:
    api_bp = Blueprint("api", __name__, url_prefix="/api/v1")

    from api.customers import customers_bp
    from api.reports import reports_bp
    from api.jobs import jobs_bp
    from api.statuses import statuses_bp
    from api.health import health_bp

    api_bp.register_blueprint(customers_bp)
    api_bp.register_blueprint(reports_bp)
    api_bp.register_blueprint(jobs_bp)
    api_bp.register_blueprint(statuses_bp)
    api_bp.register_blueprint(health_bp)

    @api_bp.errorhandler(404)
    def api_not_found(e):
        return jsonify({"error": "Not found", "code": "NOT_FOUND"}), 404

    @api_bp.errorhandler(405)
    def api_method_not_allowed(e):
        return jsonify({"error": "Method not allowed", "code": "METHOD_NOT_ALLOWED"}), 405

    @api_bp.errorhandler(500)
    def api_server_error(e):
        return jsonify({"error": "Internal server error", "code": "SERVER_ERROR"}), 500

    return api_bp
