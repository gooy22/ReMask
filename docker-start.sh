#!/bin/bash
set -euo pipefail

ROOT="/var/www/html"
PORT_VALUE="${PORT:-8080}"
DATA_DIR="${REMASK_DATA_DIR:-${RAILWAY_VOLUME_MOUNT_PATH:-/var/lib/remask}}"

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

chown -R www-data:www-data "$DATA_DIR" 2>/dev/null || true
chmod -R u+rwX,g+rwX "$DATA_DIR" 2>/dev/null || true

# The archived runtime may contain Apache module symlinks from another image.
# Railway/php-apache must run with exactly one MPM loaded.
a2dismod -f mpm_event mpm_worker 2>/dev/null || true
a2enmod mpm_prefork 2>/dev/null || true

# Railway supplies a dynamic PORT. Make Apache listen on it.
sed -ri "s/^Listen [0-9]+/Listen ${PORT_VALUE}/" /etc/apache2/ports.conf
sed -ri "s/<VirtualHost \*:[0-9]+>/<VirtualHost *:${PORT_VALUE}>/" /etc/apache2/sites-available/000-default.conf

# Serve the application root directly.
sed -ri "s#DocumentRoot .*#DocumentRoot ${ROOT}#" /etc/apache2/sites-available/000-default.conf

exec apache2-foreground
