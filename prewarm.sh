#!/usr/bin/env bash
set -euo pipefail

export TZ=Australia/Sydney
export APP_ENV=production
export FLASK_APP=app.py

cd /var/www/buz_eta_reports

# load .env so BUZ_* etc are available
[ -f .env ] && set -a && source .env && set +a

exec /var/www/buz_eta_reports/venv/bin/flask prewarm-cache
