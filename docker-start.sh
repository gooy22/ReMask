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

rm -rf "$ROOT/health"
printf '%s\n' '{"ok":true,"service":"remask"}' > "$ROOT/health"

chown -R www-data:www-data "$DATA_DIR" 2>/dev/null || true
chmod -R u+rwX,g+rwX "$DATA_DIR" 2>/dev/null || true

# Temporary read-only diagnostic for the failed Campaign POST. Emit only safe
# Meta error fields; never print job payload, token, cookies, proxy or media paths.
DIAG_JOB="$REMASK_JOB_STORAGE_DIR/20260918204521-eea59d3877.json"
if [ -f "$DIAG_JOB" ]; then
  php -r '
    $job = json_decode((string)@file_get_contents($argv[1]), true);
    if (!is_array($job)) exit(0);
    foreach (($job["items"] ?? []) as $item) {
      if ((string)($item["account_id"] ?? "") !== "act_958245207339458") continue;
      $e = is_array($item["error"]["meta_error"] ?? null) ? $item["error"]["meta_error"] : [];
      $safe = [
        "job_id" => (string)($job["id"] ?? ""),
        "account_id" => (string)($item["account_id"] ?? ""),
        "failed_step" => (string)($item["failed_step"] ?? ""),
        "message" => $e["message"] ?? null,
        "http_status" => $e["http_status"] ?? null,
        "type" => $e["type"] ?? null,
        "code" => $e["code"] ?? null,
        "subcode" => $e["subcode"] ?? null,
        "user_title" => $e["user_title"] ?? null,
        "user_message" => $e["user_message"] ?? null,
        "fbtrace_id" => $e["fbtrace_id"] ?? null,
      ];
      fwrite(STDERR, "[remask-safe-launch-error] " . json_encode($safe, JSON_UNESCAPED_SLASHES|JSON_UNESCAPED_UNICODE) . PHP_EOL);
      break;
    }
  ' "$DIAG_JOB" || true
fi

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

exec apache2-foreground
