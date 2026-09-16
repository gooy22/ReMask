FROM php:8.4-apache

RUN apt-get update \
    && apt-get install -y --no-install-recommends libcurl4-openssl-dev libpq-dev \
    && docker-php-ext-install curl pdo_pgsql \
    && a2enmod rewrite headers \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /var/www/html
COPY remask-preview-runtime.tar.gz /tmp/remask-preview-runtime.tar.gz
RUN tar -xzf /tmp/remask-preview-runtime.tar.gz -C /var/www/html \
    && rm -f /tmp/remask-preview-runtime.tar.gz \
    && mkdir -p /var/lib/remask \
    && printf '[]\n' > /var/lib/remask/accounts.json \
    && printf '[]\n' > /var/lib/remask/bundles.json \
    && chown -R www-data:www-data /var/lib/remask /var/www/html \
    && chmod 700 /var/lib/remask \
    && chmod +x /var/www/html/docker-start.sh

ENV REMASK_META_CACHE_TTL=1800 \
    META_GRAPH_API_VERSION=v26.0 \
    REMASK_PROCESS_ROLE=web

EXPOSE 80
CMD ["/var/www/html/docker-start.sh"]
