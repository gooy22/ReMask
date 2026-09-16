FROM php:8.4-apache

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update \
    && apt-get install -y --no-install-recommends libcurl4-openssl-dev libpq-dev patch xz-utils ca-certificates \
    && docker-php-ext-install curl pdo_pgsql \
    && a2enmod rewrite headers \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /var/www/html

# The previous remask-preview-runtime.tar.gz blob is truncated/corrupt in GitHub.
# Build from the stable split payload instead. Parts may be raw base64 chunks
# or JSON wrappers with a content/data/chunk/b64 field.
COPY remask-v7.part* /tmp/remask-parts/
COPY remask-preview-latest.patch /tmp/remask-preview-latest.patch
COPY docker-start.sh /tmp/docker-start.sh

RUN set -eux; \
    php -r '$out=""; $files=glob("/tmp/remask-parts/remask-v7.part*"); sort($files, SORT_NATURAL); foreach ($files as $file) { $raw=file_get_contents($file); $json=json_decode($raw, true); if (is_array($json)) { $found=false; foreach (["content","data","chunk","b64"] as $key) { if (isset($json[$key])) { $raw=$json[$key]; $found=true; break; } } if (!$found) { fwrite(STDERR, "skipping metadata-only part $file\n"); continue; } } $out .= preg_replace("/\\s+/", "", $raw); } if ($out === "") { fwrite(STDERR, "empty ReMask runtime payload\n"); exit(20); } file_put_contents("/tmp/remask-runtime.b64", $out);'; \
    base64 -d /tmp/remask-runtime.b64 > /tmp/remask-runtime.archive; \
    if xz -t /tmp/remask-runtime.archive; then tar -xJf /tmp/remask-runtime.archive -C /var/www/html; \
    elif gzip -t /tmp/remask-runtime.archive; then tar -xzf /tmp/remask-runtime.archive -C /var/www/html; \
    else echo "Unsupported or corrupt ReMask runtime archive" >&2; exit 21; fi; \
    patch -p1 -N --batch -d /var/www/html < /tmp/remask-preview-latest.patch || true; \
    mkdir -p /var/www/html/health /var/lib/remask /var/lib/remask/jobs /var/lib/remask/bundles /var/lib/remask/meta-cache /var/lib/remask/job-media; \
    if [ ! -f /var/www/html/health/index.php ]; then printf '%s\n' '<?php http_response_code(200); header("Content-Type: application/json"); echo json_encode(["ok"=>true,"service"=>"remask","rev"=>getenv("REMASK_DEPLOY_REV")]);' > /var/www/html/health/index.php; fi; \
    [ -f /var/www/html/index.php ]; \
    [ -f /var/www/html/launch.php ]; \
    cp /tmp/docker-start.sh /var/www/html/docker-start.sh; \
    printf '[]\n' > /var/lib/remask/accounts.json; \
    printf '[]\n' > /var/lib/remask/bundles.json; \
    chown -R www-data:www-data /var/lib/remask /var/www/html; \
    chmod 700 /var/lib/remask; \
    chmod +x /var/www/html/docker-start.sh; \
    rm -rf /tmp/remask-parts /tmp/remask-preview-latest.patch /tmp/remask-runtime.b64 /tmp/remask-runtime.archive /tmp/docker-start.sh

ENV REMASK_META_CACHE_TTL=1800 \
    META_GRAPH_API_VERSION=v26.0 \
    REMASK_PROCESS_ROLE=web

EXPOSE 80
CMD ["/var/www/html/docker-start.sh"]
