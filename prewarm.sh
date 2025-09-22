#!/usr/bin/env bash
set -euo pipefail

cd /var/www/buz_eta_reports
export APP_ENV=production
export FLASK_APP=app.py
# load .env so BUZ_* etc are available
[ -f .env ] && set -a && source .env && set +a

exec /var/www/buz_eta_reports/venv/bin/flask prewarm-cache
