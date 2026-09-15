FROM php:8.4-apache

RUN apt-get update \
    && apt-get install -y --no-install-recommends libcurl4-openssl-dev ca-certificates xz-utils \
    && docker-php-ext-install curl \
    && a2enmod rewrite headers \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /var/www/html

COPY remask-v7.part00 remask-v7.part01 remask-v7.part02 remask-v7.part03 remask-v7.part04 remask-v7.part05 remask-v7.part06 remask-v7.part07 remask-v7.part08 /tmp/remask-payload/

RUN rm -rf /var/www/html/* \
    && cat /tmp/remask-payload/remask-v7.part00 \
           /tmp/remask-payload/remask-v7.part01 \
           /tmp/remask-payload/remask-v7.part02 \
           /tmp/remask-payload/remask-v7.part03 \
           /tmp/remask-payload/remask-v7.part04 \
           /tmp/remask-payload/remask-v7.part05 \
           /tmp/remask-payload/remask-v7.part06 \
           /tmp/remask-payload/remask-v7.part07 \
           /tmp/remask-payload/remask-v7.part08 \
       | base64 -d \
       | xz -d \
       | tar -x -C /var/www/html \
    && rm -rf /tmp/remask-payload \
    && chown -R www-data:www-data /var/www/html

COPY docker-start.sh /var/www/html/docker-start.sh
RUN chmod +x /var/www/html/docker-start.sh \
    && chown www-data:www-data /var/www/html/docker-start.sh

ENV REMASK_META_CACHE_TTL=1800 \
    META_GRAPH_API_VERSION=v26.0

EXPOSE 80
CMD ["/var/www/html/docker-start.sh"]
