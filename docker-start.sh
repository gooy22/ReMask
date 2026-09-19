#!/bin/sh
set -eu

ROOT="/var/www/html"
DATA_DIR="${REMASK_DATA_DIR:-${RAILWAY_VOLUME_MOUNT_PATH:-/var/lib/remask}}"
WORKER_INTERVAL="${REMASK_WORKER_INTERVAL_SECONDS:-15}"
PROCESS_ROLE="${REMASK_PROCESS_ROLE:-web}"

export REMASK_DATA_DIR="$DATA_DIR"
export REMASK_ACCOUNTS_FILE="${REMASK_ACCOUNTS_FILE:-$DATA_DIR/accounts.json}"
export REMASK_JOB_STORAGE_DIR="${REMASK_JOB_STORAGE_DIR:-$DATA_DIR/jobs}"
export REMASK_JOBS_DIR="${REMASK_JOBS_DIR:-$REMASK_JOB_STORAGE_DIR}"
export REMASK_BUNDLE_STORAGE_DIR="${REMASK_BUNDLE_STORAGE_DIR:-$DATA_DIR/bundles}"
export REMASK_BUNDLES_FILE="${REMASK_BUNDLES_FILE:-$DATA_DIR/bundles.json}"
export REMASK_META_CACHE_DIR="${REMASK_META_CACHE_DIR:-$DATA_DIR/meta-cache}"
export REMASK_META_USAGE_FILE="${REMASK_META_USAGE_FILE:-$DATA_DIR/meta-usage.json}"
export REMASK_JOB_MEDIA_DIR="${REMASK_JOB_MEDIA_DIR:-$DATA_DIR/job-media}"
export REMASK_MEDIA_LIBRARY_DIR="${REMASK_MEDIA_LIBRARY_DIR:-$DATA_DIR/media-library}"

mkdir -p   "$DATA_DIR"   "$REMASK_JOB_STORAGE_DIR"   "$REMASK_BUNDLE_STORAGE_DIR"   "$REMASK_META_CACHE_DIR"   "$REMASK_JOB_MEDIA_DIR"   "$REMASK_MEDIA_LIBRARY_DIR"

[ -s "$REMASK_ACCOUNTS_FILE" ] || printf '[]\n' > "$REMASK_ACCOUNTS_FILE"
[ -s "$REMASK_BUNDLES_FILE" ] || printf '[]\n' > "$REMASK_BUNDLES_FILE"
[ -s "$REMASK_META_USAGE_FILE" ] || printf '{}\n' > "$REMASK_META_USAGE_FILE"

chown -R www-data:www-data "$DATA_DIR" 2>/dev/null || true
chmod -R u+rwX,g+rwX "$DATA_DIR" 2>/dev/null || true

# Keep Apache on the same known-good port used by the Railway public domain.
a2dismod -f mpm_event mpm_worker 2>/dev/null || true
a2enmod mpm_prefork 2>/dev/null || true
printf '%s\n' 'ServerName localhost' > /etc/apache2/conf-available/remask-servername.conf
a2enconf remask-servername 2>/dev/null || true
printf '%s\n' 'Listen 80' > /etc/apache2/ports.conf
sed -ri "s#DocumentRoot .*#DocumentRoot ${ROOT}#" /etc/apache2/sites-available/000-default.conf
sed -ri 's/<VirtualHost \*:[0-9]+>/<VirtualHost *:80>/' /etc/apache2/sites-available/000-default.conf

# v104 jobs must run through the actual worker installed by railway-worker-overlay.php.
# Keep it independent from the web request so the browser can be closed safely.
case "${REMASK_JOB_EXECUTION_MODE:-browser}" in
  worker|background)
    if [ -f "$ROOT/bin/remask-worker.php" ]; then
      (
        echo "[$(date -u +%FT%TZ)] starting remask background worker loop" >> "$DATA_DIR/worker.log"
        while true; do
          php "$ROOT/bin/remask-worker.php" >> "$DATA_DIR/worker.log" 2>&1 || true
          sleep "$WORKER_INTERVAL"
        done
      ) &
      echo "$!" > "$DATA_DIR/worker.pid" || true
    fi
    ;;
esac

if [ -n "${REMASK_DIAG_PROFILE:-}" ] && [ -f "$ROOT/bin/remask-preflight-diag.php" ]; then
  php "$ROOT/bin/remask-preflight-diag.php" >&2 || true
fi

exec apache2-foreground
