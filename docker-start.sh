#!/bin/bash
set -euo pipefail
umask 0077

ROOT="/var/www/html"
PORT_VALUE="${PORT:-80}"
DATA_DIR="${REMASK_DATA_DIR:-${RAILWAY_VOLUME_MOUNT_PATH:-/var/lib/remask}}"
WORKER_INTERVAL="${REMASK_WORKER_INTERVAL_SECONDS:-15}"
PYTHON_WORKER_PORT="${REMASK_LOCAL_WORKER_PORT:-8081}"

# Production must fail closed; keys are durable and must not change on restart.
if [ -z "${REMASK_PASSWORD:-}" ] || [ "${REMASK_PASSWORD:-}" = "change-me-now" ]; then
  echo "REMASK_PASSWORD must be configured" >&2; exit 1
fi
if [ -z "${REMASK_CREDENTIALS_KEY:-}" ]; then
  echo "REMASK_CREDENTIALS_KEY must be configured" >&2; exit 1
fi
export REMASK_ENCRYPT_PROFILE_JSON=1
export REMASK_HTTP_ONLY=1

export REMASK_DATA_DIR="$DATA_DIR"
export REMASK_ACCOUNTS_FILE="${REMASK_ACCOUNTS_FILE:-$DATA_DIR/accounts.json}"
export REMASK_JOB_STORAGE_DIR="${REMASK_JOB_STORAGE_DIR:-$DATA_DIR/jobs}"
export REMASK_BUNDLE_STORAGE_DIR="${REMASK_BUNDLE_STORAGE_DIR:-$DATA_DIR/bundles}"
export REMASK_META_CACHE_DIR="${REMASK_META_CACHE_DIR:-$DATA_DIR/meta-cache}"
export REMASK_META_USAGE_FILE="${REMASK_META_USAGE_FILE:-$DATA_DIR/meta-usage.json}"
export REMASK_JOB_MEDIA_DIR="${REMASK_JOB_MEDIA_DIR:-$DATA_DIR/job-media}"
export REMASK_MEDIA_LIBRARY_DIR="${REMASK_MEDIA_LIBRARY_DIR:-$DATA_DIR/media-library}"
export REMASK_CREATIVE_PRESET_DIR="${REMASK_CREATIVE_PRESET_DIR:-$DATA_DIR/creative-presets}"
export REMASK_PYTHON_STATE_DIR="${REMASK_PYTHON_STATE_DIR:-$DATA_DIR/python-worker-jobs}"
export REMASK_JOB_DB="${REMASK_JOB_DB:-$DATA_DIR/python-worker/jobs.sqlite3}"
export REMASK_PHP_SESSION_DIR="${REMASK_PHP_SESSION_DIR:-$DATA_DIR/php-sessions}"

if [ -z "${REMASK_INTERNAL_KEY:-}" ]; then
  export REMASK_INTERNAL_KEY="$(/opt/remask-venv/bin/python -c 'import secrets; print(secrets.token_hex(24))')"
fi
if [ -z "${REMASK_WORKER_API_KEY:-}" ]; then
  export REMASK_WORKER_API_KEY="$(/opt/remask-venv/bin/python -c 'import secrets; print(secrets.token_hex(24))')"
fi

# Keep the UI and provisioning worker on the same deployed revision by default.
# An external worker is allowed only when explicitly opted in with the new
# REMASK_FORCE_EXTERNAL_PYTHON_WORKER flag. This prevents a stale external
# worker from serving jobs after the web service has already deployed newer code.
USE_EXTERNAL_PYTHON_WORKER=0
if [ "${REMASK_FORCE_EXTERNAL_PYTHON_WORKER:-0}" = "1" ]; then
  USE_EXTERNAL_PYTHON_WORKER=1
fi

if [ "$USE_EXTERNAL_PYTHON_WORKER" != "1" ]; then
  export REMASK_PYTHON_WORKER_URL="http://127.0.0.1:$PYTHON_WORKER_PORT"
  export REMASK_PROFILE_RESOLVER_URL="http://127.0.0.1/ajax/pythonProfileContext.php"
  unset REMASK_STATE_URL
  unset REMASK_JOB_BRIDGE_URL
fi

mkdir -p \
  "$DATA_DIR" \
  "$REMASK_JOB_STORAGE_DIR" \
  "$REMASK_BUNDLE_STORAGE_DIR" \
  "$REMASK_META_CACHE_DIR" \
  "$REMASK_JOB_MEDIA_DIR" \
  "$REMASK_MEDIA_LIBRARY_DIR" \
  "$REMASK_CREATIVE_PRESET_DIR" \
  "$REMASK_PYTHON_STATE_DIR" \
  "$REMASK_PHP_SESSION_DIR" \
  "$(dirname "$REMASK_JOB_DB")"

touch "$REMASK_META_USAGE_FILE"
[ -s "$REMASK_META_USAGE_FILE" ] || printf '{}\n' > "$REMASK_META_USAGE_FILE"

# Keep legacy runtime paths volume-backed too. Older ReMask classes may still
# reference /var/www/html/accounts.json or bundles.json directly.
persist_legacy_file() {
  local legacy="$1"
  local target="$2"
  local initial="$3"

  mkdir -p "$(dirname "$target")"

  if [ ! -e "$target" ]; then
    if [ -f "$legacy" ] && [ ! -L "$legacy" ] && [ -s "$legacy" ]; then
      cp "$legacy" "$target"
    else
      printf '%s\n' "$initial" > "$target"
    fi
  fi

  rm -f "$legacy"
  ln -s "$target" "$legacy"
}

persist_legacy_file "$ROOT/accounts.json" "$REMASK_ACCOUNTS_FILE" '[]'
if [ -d "$ROOT/data" ]; then
  persist_legacy_file "$ROOT/data/accounts.json" "$REMASK_ACCOUNTS_FILE" '[]'
fi
persist_legacy_file "$ROOT/bundles.json" "$DATA_DIR/bundles.json" '[]'

rm -rf "$ROOT/health"
printf '%s\n' '{"ok":true,"service":"remask"}' > "$ROOT/health"
chmod 644 "$ROOT/health"

# Persistent files were normalized by the previous live runtime. On each start,
# hand only active storage roots and top-level state files to www-data. A recursive
# chown/chmod of the whole volume here delays Apache and every health check.
STORAGE_OWNERSHIP_PATHS=(
  "$DATA_DIR"
  "$REMASK_JOB_STORAGE_DIR"
  "$REMASK_BUNDLE_STORAGE_DIR"
  "$REMASK_META_CACHE_DIR"
  "$REMASK_JOB_MEDIA_DIR"
  "$REMASK_MEDIA_LIBRARY_DIR"
  "$REMASK_CREATIVE_PRESET_DIR"
  "$REMASK_PYTHON_STATE_DIR"
  "$(dirname "$REMASK_JOB_DB")"
  "$REMASK_PHP_SESSION_DIR"
  "$REMASK_ACCOUNTS_FILE"
  "$REMASK_META_USAGE_FILE"
  "$DATA_DIR/bundles.json"
  "$REMASK_JOB_DB"
)
for path in "${STORAGE_OWNERSHIP_PATHS[@]}"; do
  [ -e "$path" ] || continue
  chown www-data:www-data -- "$path"
  if [ -d "$path" ]; then
    chmod 700 -- "$path"
  else
    chmod 600 -- "$path"
  fi
done

# Run the one-time account-store migration as its storage owner. This keeps
# atomic replacements owned by www-data and removes the second volume-wide scan.
runuser -u www-data -- php -r 'require $argv[1]."/settings.php";require $argv[1]."/classes/AccountStoreFactory.php";$store=AccountStoreFactory::create(ACCOUNTSFILENAME);$profiles=$store->deserialize();if ($store instanceof FbAccountSerializer) $store->ensureEncrypted();echo "[profile-storage] ready count=".count($store->deserialize())." encrypted=1 key_encoding=".CredentialVault::environmentEncoding()."\n";' "$ROOT"
chmod 700 "$REMASK_PHP_SESSION_DIR"
chown www-data:www-data "$REMASK_PHP_SESSION_DIR"

# Persist PHP login sessions across Railway container replacements. Without
# this, an open Workspace page keeps its browser cookie but the server-side
# session file disappears on every deploy, causing pythonWorkerJobs.php to
# return 401 until the user logs in again.
mkdir -p /usr/local/etc/php/conf.d
cat > /usr/local/etc/php/conf.d/remask-session.ini <<EOF
session.save_handler = files
session.save_path = "$REMASK_PHP_SESSION_DIR"
session.gc_maxlifetime = 604800
session.cookie_lifetime = 604800
session.cookie_httponly = 1
session.cookie_samesite = Lax
EOF

echo "[ui-session] configured save_path=$REMASK_PHP_SESSION_DIR persistent=1"

if [ "${REMASK_JOB_EXECUTION_MODE:-browser}" = "background" ] && [ -f "$ROOT/bin/remask-worker.php" ]; then
  (
    echo "[$(date -u +%FT%TZ)] starting remask background worker loop"
    while true; do
      php "$ROOT/bin/remask-worker.php" || true
      sleep "$WORKER_INTERVAL"
    done
  ) &
  echo "$!" > "$DATA_DIR/worker.pid" || true
fi

if [ "$USE_EXTERNAL_PYTHON_WORKER" != "1" ]; then
  (
    cd /opt/remask-python
    # PHP and Python share private 0600 files. Use the volume owner so atomic
    # worker replacements remain readable by PHP without broadening modes.
    exec runuser -u www-data -- /opt/remask-venv/bin/uvicorn main:app --host 127.0.0.1 --port "$PYTHON_WORKER_PORT" --workers 1
  ) &
  PYTHON_WORKER_PID="$!"
  echo "$PYTHON_WORKER_PID" > "$DATA_DIR/python-worker.pid"

  WORKER_HEALTH_OK=0
  for _ in $(seq 1 40); do
    if ! kill -0 "$PYTHON_WORKER_PID" 2>/dev/null; then
      break
    fi
    if /opt/remask-venv/bin/python - "$PYTHON_WORKER_PORT" <<'PY'
import sys
import urllib.request

port = int(sys.argv[1])
try:
    with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=0.5) as response:
        raise SystemExit(0 if response.status == 200 else 1)
except Exception:
    raise SystemExit(1)
PY
    then
      WORKER_HEALTH_OK=1
      break
    fi
    sleep 0.25
  done

  if [ "$WORKER_HEALTH_OK" != "1" ]; then
    echo "Embedded Python worker did not become healthy during initial probe window; watchdog will recover it" >&2
  fi

  # Live inventory is verified after Apache starts, because ProfileResolver
  # calls the local PHP context endpoint.

  # Keep monitoring the HTTP-only worker after startup too.
  # Restart only after three consecutive failed health probes to avoid killing
  # the worker on a single transient event-loop stall.
  (
    FAIL_COUNT=0
    while true; do
      if /opt/remask-venv/bin/python - "$PYTHON_WORKER_PORT" <<'PY'
import sys
import urllib.request

port = int(sys.argv[1])
try:
    with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=1.0) as response:
        raise SystemExit(0 if response.status == 200 else 1)
except Exception:
    raise SystemExit(1)
PY
      then
        FAIL_COUNT=0
        sleep 5
        continue
      fi

      FAIL_COUNT=$((FAIL_COUNT + 1))
      if [ "$FAIL_COUNT" -lt 3 ]; then
        sleep 2
        continue
      fi

      CURRENT_PID=""
      if [ -f "$DATA_DIR/python-worker.pid" ]; then
        CURRENT_PID="$(cat "$DATA_DIR/python-worker.pid" 2>/dev/null || true)"
      fi

      if [ -n "$CURRENT_PID" ] && kill -0 "$CURRENT_PID" 2>/dev/null; then
        echo "[$(date -u +%FT%TZ)] embedded Python worker unhealthy; terminating pid=$CURRENT_PID" >&2
        kill -TERM "$CURRENT_PID" 2>/dev/null || true
        for _ in $(seq 1 12); do
          if ! kill -0 "$CURRENT_PID" 2>/dev/null; then
            break
          fi
          sleep 0.25
        done
        if kill -0 "$CURRENT_PID" 2>/dev/null; then
          kill -KILL "$CURRENT_PID" 2>/dev/null || true
        fi
      fi

      echo "[$(date -u +%FT%TZ)] restarting embedded Python worker after health failure" >&2
      (
        cd /opt/remask-python
        exec runuser -u www-data -- /opt/remask-venv/bin/uvicorn main:app --host 127.0.0.1 --port "$PYTHON_WORKER_PORT" --workers 1
      ) &
      echo "$!" > "$DATA_DIR/python-worker.pid"
      FAIL_COUNT=0
      sleep 5
    done
  ) &
  echo "$!" > "$DATA_DIR/python-worker-watchdog.pid" || true
fi

a2dismod -f mpm_event mpm_worker 2>/dev/null || true
a2enmod mpm_prefork 2>/dev/null || true

printf '%s\n' 'ServerName localhost' > /etc/apache2/conf-available/remask-servername.conf
a2enconf remask-servername 2>/dev/null || true

{
  printf '%s\n' 'Listen 80'
  if [ "$PORT_VALUE" != "80" ]; then printf 'Listen %s\n' "$PORT_VALUE"; fi
} > /etc/apache2/ports.conf

sed -ri "s#DocumentRoot .*#DocumentRoot ${ROOT}#" /etc/apache2/sites-available/000-default.conf
sed -ri "s/<VirtualHost \*:[0-9]+>/<VirtualHost *:80>/" /etc/apache2/sites-available/000-default.conf


# Remove abandoned atomic temp files outside the readiness path. A slow volume
# scan must never keep the web server from opening its port after deployment.
(find "$DATA_DIR" -xdev -type f -name '*.tmp-*' -mmin +1440 -delete) >/dev/null 2>&1 &

exec apache2-foreground 2> >(
  while IFS= read -r line; do
    if [[ "$line" == *":notice]"* ]]; then
      printf '%s\n' "$line"
    else
      printf '%s\n' "$line" >&2
    fi
  done
)
