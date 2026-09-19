#!/bin/sh
set -eu

APP_PORT="${PORT:-80}"
PROCESS_ROLE="${REMASK_PROCESS_ROLE:-web}"

case "$PROCESS_ROLE" in
  web|worker|all) ;;
  *) echo "ERROR: REMASK_PROCESS_ROLE must be web, worker or all (got: $PROCESS_ROLE)" >&2; exit 2 ;;
esac

if [ -n "${REMASK_DATA_DIR:-}" ]; then
  DATA_DIR="${REMASK_DATA_DIR%/}"
elif [ -n "${RAILWAY_VOLUME_MOUNT_PATH:-}" ]; then
  DATA_DIR="${RAILWAY_VOLUME_MOUNT_PATH%/}"
else
  DATA_DIR="/var/lib/remask"
fi

export REMASK_DATA_DIR="$DATA_DIR"
export REMASK_ACCOUNTS_FILE="${REMASK_ACCOUNTS_FILE:-$DATA_DIR/accounts.json}"
export REMASK_JOBS_DIR="${REMASK_JOBS_DIR:-$DATA_DIR/jobs}"
export REMASK_BUNDLES_FILE="${REMASK_BUNDLES_FILE:-$DATA_DIR/bundles.json}"
export REMASK_META_CACHE_DIR="${REMASK_META_CACHE_DIR:-$DATA_DIR/meta-cache}"
export REMASK_META_USAGE_FILE="${REMASK_META_USAGE_FILE:-$DATA_DIR/meta-usage.json}"
export REMASK_JOB_MEDIA_DIR="${REMASK_JOB_MEDIA_DIR:-$DATA_DIR/job-media}"
export REMASK_MEDIA_LIBRARY_DIR="${REMASK_MEDIA_LIBRARY_DIR:-$DATA_DIR/media-library}"

mkdir -p \
  "$DATA_DIR" \
  "$REMASK_JOBS_DIR" \
  "$REMASK_META_CACHE_DIR" \
  "$REMASK_JOB_MEDIA_DIR" \
  "$REMASK_MEDIA_LIBRARY_DIR"

[ -s "$REMASK_ACCOUNTS_FILE" ] || printf '[]\n' > "$REMASK_ACCOUNTS_FILE"
[ -s "$REMASK_BUNDLES_FILE" ] || printf '[]\n' > "$REMASK_BUNDLES_FILE"
[ -s "$REMASK_META_USAGE_FILE" ] || printf '{}\n' > "$REMASK_META_USAGE_FILE"

chown -R www-data:www-data "$DATA_DIR"
chmod 700 "$DATA_DIR" "$REMASK_JOBS_DIR" "$REMASK_META_CACHE_DIR" "$REMASK_JOB_MEDIA_DIR" "$REMASK_MEDIA_LIBRARY_DIR"
chmod 600 "$REMASK_ACCOUNTS_FILE" "$REMASK_BUNDLES_FILE" "$REMASK_META_USAGE_FILE"

if [ -n "${RAILWAY_ENVIRONMENT_ID:-}" ] && [ -z "${RAILWAY_VOLUME_MOUNT_PATH:-}" ] && [ "$PROCESS_ROLE" != "worker" ]; then
  echo "WARNING: ReMask is running on Railway without a detected persistent Volume." >&2
fi

if [ "${REMASK_AUTO_MIGRATE:-0}" = "1" ]; then
  echo "[ReMask runtime] applying PostgreSQL migrations"
  php /var/www/html/bin/migrate.php
fi

require_postgres_worker_stores() {
  [ "${REMASK_JOB_STORE:-json}" = "postgres" ] || return 1
  [ "${REMASK_PROFILE_STORE:-json}" = "postgres" ] || return 1
  [ "${REMASK_JOB_MEDIA_STORE:-file}" = "postgres" ] || return 1
  [ "${REMASK_META_CACHE_STORE:-file}" = "postgres" ] || return 1
  [ "${REMASK_META_USAGE_STORE:-file}" = "postgres" ] || return 1
  [ "${REMASK_ACTIVITY_LOG_STORE:-file}" = "postgres" ] || return 1
  [ -n "${REMASK_CREDENTIALS_KEY:-}" ] || return 1
}

if [ "$PROCESS_ROLE" = "worker" ]; then
  if ! require_postgres_worker_stores; then
    echo "ERROR: a separate worker requires PostgreSQL job/profile/media/cache/usage/activity stores and REMASK_CREDENTIALS_KEY." >&2
    exit 3
  fi
  echo "[ReMask runtime] starting dedicated worker"
  exec su -s /bin/sh -c 'exec php /var/www/html/worker.php' www-data
fi

# php:apache must run a single MPM. Some package/module combinations can leave
# event/worker enabled alongside prefork, which makes apache2-foreground abort
# with AH00534: More than one MPM loaded.
a2dismod -f mpm_event mpm_worker 2>/dev/null || true
a2enmod mpm_prefork 2>/dev/null || true

sed -ri "s/^Listen [0-9]+$/Listen ${APP_PORT}/" /etc/apache2/ports.conf
sed -ri "s/<VirtualHost \\*:[0-9]+>/<VirtualHost *:${APP_PORT}>/" /etc/apache2/sites-available/000-default.conf

# Transitional all-in-one mode keeps JSON/file stores in the same container.
# The final PostgreSQL topology should use REMASK_PROCESS_ROLE=web and a
# separate REMASK_PROCESS_ROLE=worker service.
if [ "$PROCESS_ROLE" = "all" ] || { [ "${REMASK_JOB_EXECUTION_MODE:-browser}" = "worker" ] && [ "${REMASK_JOB_STORE:-json}" != "postgres" ]; }; then
  WORKER_COUNT="${REMASK_WORKER_PROCESSES:-1}"
  case "$WORKER_COUNT" in ''|*[!0-9]*) WORKER_COUNT=1 ;; esac
  [ "$WORKER_COUNT" -ge 1 ] || WORKER_COUNT=1
  [ "$WORKER_COUNT" -le 8 ] || WORKER_COUNT=8
  i=1
  while [ "$i" -le "$WORKER_COUNT" ]; do
    echo "[ReMask runtime] starting embedded worker $i/$WORKER_COUNT"
    su -s /bin/sh -c 'exec php /var/www/html/worker.php' www-data &
    i=$((i + 1))
  done
fi

exec apache2-foreground
