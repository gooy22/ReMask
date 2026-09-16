FROM php:8.4-apache

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update \
    && apt-get install -y --no-install-recommends libcurl4-openssl-dev libpq-dev patch xz-utils ca-certificates \
    && docker-php-ext-install curl pdo_pgsql \
    && a2enmod rewrite headers \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /var/www/html

# The root remask-v7.part* payload is incomplete/corrupt.
# Use the verified clean-preview payload from .deploy/clean-preview-valid.
COPY .deploy/clean-preview-valid/runtime.b64.* /tmp/remask-parts/
COPY remask-preview-latest.patch /tmp/remask-preview-latest.patch
COPY railway-launch-overlay.php /tmp/railway-launch-overlay.php
COPY docker-start.sh /tmp/docker-start.sh

RUN set -eux; \
    php -r '$out=""; $files=glob("/tmp/remask-parts/runtime.b64.*"); sort($files, SORT_NATURAL); foreach ($files as $file) { $out .= preg_replace("/\\s+/", "", file_get_contents($file)); } if ($out === "") { fwrite(STDERR, "empty ReMask runtime payload\n"); exit(20); } file_put_contents("/tmp/remask-runtime.b64", $out);'; \
    base64 -d /tmp/remask-runtime.b64 > /tmp/remask-runtime.archive; \
    echo "84b51ad4c062e45a13fd61b1ca8c66d0d1896b2bab2ebf2513dfd782998fdd95  /tmp/remask-runtime.archive" | sha256sum -c -; \
    if xz -t /tmp/remask-runtime.archive; then tar -xJf /tmp/remask-runtime.archive -C /var/www/html; \
    elif gzip -t /tmp/remask-runtime.archive; then tar -xzf /tmp/remask-runtime.archive -C /var/www/html; \
    else echo "Unsupported or corrupt ReMask runtime archive" >&2; exit 21; fi; \
    patch -p1 -N --batch -d /var/www/html < /tmp/remask-preview-latest.patch || true; \
    php /tmp/railway-launch-overlay.php; \
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
    rm -rf /tmp/remask-parts /tmp/remask-preview-latest.patch /tmp/railway-launch-overlay.php /tmp/remask-runtime.b64 /tmp/remask-runtime.archive /tmp/docker-start.sh

ENV REMASK_META_CACHE_TTL=1800 \
    META_GRAPH_API_VERSION=v26.0 \
    REMASK_PROCESS_ROLE=web

EXPOSE 80
CMD ["/var/www/html/docker-start.sh"]
