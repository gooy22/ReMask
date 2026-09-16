FROM php:8.4-apache

RUN apt-get update \
    && apt-get install -y --no-install-recommends libcurl4-openssl-dev libpq-dev xz-utils coreutils \
    && docker-php-ext-install curl pdo_pgsql \
    && a2enmod rewrite headers \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /var/www/html
COPY preview/ /tmp/remask-preview/

RUN set -eux; \
    : > /tmp/runtime.b64; \
    cat /tmp/remask-preview/runtime.part00 /tmp/remask-preview/runtime.part01 /tmp/remask-preview/runtime.part02 /tmp/remask-preview/runtime.part03 /tmp/remask-preview/runtime.part04 /tmp/remask-preview/runtime.part05 >> /tmp/runtime.b64; \
    cat /tmp/remask-preview/fix06.0 /tmp/remask-preview/fix06.1 >> /tmp/runtime.b64; \
    cat /tmp/remask-preview/runtime.part07 /tmp/remask-preview/runtime.part08 /tmp/remask-preview/runtime.part09 /tmp/remask-preview/runtime.part10 /tmp/remask-preview/runtime.part11 /tmp/remask-preview/runtime.part12 /tmp/remask-preview/runtime.part13 /tmp/remask-preview/runtime.part14 /tmp/remask-preview/runtime.part15 /tmp/remask-preview/runtime.part16 /tmp/remask-preview/runtime.part17 /tmp/remask-preview/runtime.part18 /tmp/remask-preview/runtime.part19 /tmp/remask-preview/runtime.part20 >> /tmp/runtime.b64; \
    base64 -d /tmp/runtime.b64 > /tmp/runtime.tar.xz; \
    echo '84b51ad4c062e45a13fd61b1ca8c66d0d1896b2bab2ebf2513dfd782998fdd95  /tmp/runtime.tar.xz' | sha256sum -c -; \
    tar -xJf /tmp/runtime.tar.xz -C /var/www/html; \
    rm -rf /tmp/runtime.b64 /tmp/runtime.tar.xz /tmp/remask-preview; \
    mkdir -p /var/lib/remask; \
    printf '[]\n' > /var/lib/remask/accounts.json; \
    printf '[]\n' > /var/lib/remask/bundles.json; \
    chown -R www-data:www-data /var/lib/remask /var/www/html; \
    chmod 700 /var/lib/remask; \
    chmod +x /var/www/html/docker-start.sh

ENV REMASK_META_CACHE_TTL=1800 \
    META_GRAPH_API_VERSION=v26.0 \
    REMASK_PROCESS_ROLE=web

EXPOSE 80
CMD ["/var/www/html/docker-start.sh"]
