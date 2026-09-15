FROM php:8.4-apache

RUN apt-get update \
    && apt-get install -y --no-install-recommends libcurl4-openssl-dev ca-certificates gzip \
    && docker-php-ext-install curl \
    && a2enmod rewrite headers \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /var/www/html

COPY remask-v7.part00 /tmp/remask-v7.part00
COPY remask-v7.part01 /tmp/remask-v7.part01
COPY remask-v7.part02 /tmp/remask-v7.part02
RUN cat /tmp/remask-v7.part00 /tmp/remask-v7.part01 /tmp/remask-v7.part02 \
    | base64 -d \
    | tar -xzf - -C /var/www/html \
    && rm -f /tmp/remask-v7.part00 /tmp/remask-v7.part01 /tmp/remask-v7.part02 \
    && chmod +x /var/www/html/docker-start.sh \
    && chown -R www-data:www-data /var/www/html

ENV REMASK_META_CACHE_TTL=1800 \
    META_GRAPH_API_VERSION=v26.0

EXPOSE 80
CMD ["/var/www/html/docker-start.sh"]
