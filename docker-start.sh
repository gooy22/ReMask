#!/bin/bash
set -euo pipefail

ROOT="/var/www/html"
PORT_VALUE="${PORT:-80}"
DATA_DIR="${REMASK_DATA_DIR:-${RAILWAY_VOLUME_MOUNT_PATH:-/var/lib/remask}}"
WORKER_INTERVAL="${REMASK_WORKER_INTERVAL_SECONDS:-15}"

export REMASK_DATA_DIR="$DATA_DIR"
export REMASK_ACCOUNTS_FILE="${REMASK_ACCOUNTS_FILE:-$DATA_DIR/accounts.json}"
export REMASK_JOB_STORAGE_DIR="${REMASK_JOB_STORAGE_DIR:-$DATA_DIR/jobs}"
export REMASK_BUNDLE_STORAGE_DIR="${REMASK_BUNDLE_STORAGE_DIR:-$DATA_DIR/bundles}"
export REMASK_META_CACHE_DIR="${REMASK_META_CACHE_DIR:-$DATA_DIR/meta-cache}"
export REMASK_META_USAGE_FILE="${REMASK_META_USAGE_FILE:-$DATA_DIR/meta-usage.json}"
export REMASK_JOB_MEDIA_DIR="${REMASK_JOB_MEDIA_DIR:-$DATA_DIR/job-media}"

mkdir -p \
  "$DATA_DIR" \
  "$REMASK_JOB_STORAGE_DIR" \
  "$REMASK_BUNDLE_STORAGE_DIR" \
  "$REMASK_META_CACHE_DIR" \
  "$REMASK_JOB_MEDIA_DIR"

touch "$REMASK_ACCOUNTS_FILE" "$REMASK_META_USAGE_FILE"
[ -s "$REMASK_ACCOUNTS_FILE" ] || printf '[]\n' > "$REMASK_ACCOUNTS_FILE"
[ -s "$REMASK_META_USAGE_FILE" ] || printf '{}\n' > "$REMASK_META_USAGE_FILE"

# Railway may call either /health or /health/. Serve both without 301/404.
rm -rf "$ROOT/health"
mkdir -p "$ROOT/health"
printf '%s\n' '{"ok":true,"service":"remask"}' > "$ROOT/health/index.html"
printf '%s\n' '<Directory "/var/www/html/health">' '  DirectorySlash Off' '  Require all granted' '</Directory>' > /etc/apache2/conf-available/remask-health.conf
a2enconf remask-health 2>/dev/null || true

chown -R www-data:www-data "$DATA_DIR" 2>/dev/null || true
chmod -R u+rwX,g+rwX "$DATA_DIR" 2>/dev/null || true

if [ "${REMASK_JOB_EXECUTION_MODE:-browser}" = "background" ] && [ -f "$ROOT/bin/remask-worker.php" ]; then
  (
    echo "[$(date -u +%FT%TZ)] starting remask background worker loop" >> "$DATA_DIR/worker.log"
    while true; do
      php "$ROOT/bin/remask-worker.php" >> "$DATA_DIR/worker.log" 2>&1 || true
      sleep "$WORKER_INTERVAL"
    done
  ) &
  echo "$!" > "$DATA_DIR/worker.pid" || true
fi

# The archived runtime may contain Apache module symlinks from another image.
# Railway/php-apache must run with exactly one MPM loaded.
a2dismod -f mpm_event mpm_worker 2>/dev/null || true
a2enmod mpm_prefork 2>/dev/null || true

# Suppress Apache FQDN warning and keep a stable public port.
printf '%s\n' 'ServerName localhost' > /etc/apache2/conf-available/remask-servername.conf
a2enconf remask-servername 2>/dev/null || true

# The Railway domain is routed to targetPort 80. Keep Apache listening on 80.
# Also listen on Railway $PORT if it is different, so health/proxy checks work either way.
{
  printf '%s\n' 'Listen 80'
  if [ "$PORT_VALUE" != "80" ]; then printf 'Listen %s\n' "$PORT_VALUE"; fi
} > /etc/apache2/ports.conf

# Serve the application root directly.
sed -ri "s#DocumentRoot .*#DocumentRoot ${ROOT}#" /etc/apache2/sites-available/000-default.conf
sed -ri "s/<VirtualHost \*:[0-9]+>/<VirtualHost *:80>/" /etc/apache2/sites-available/000-default.conf

exec apache2-foreground
