FROM php:8.3-cli
RUN apt-get update \
 && apt-get install -y --no-install-recommends unzip git ca-certificates curl \
 && curl -fsSL -o /usr/local/bin/phpunit https://phar.phpunit.de/phpunit-11.phar \
 && curl -fsSL -o /usr/local/bin/composer https://getcomposer.org/download/latest-2.x/composer.phar \
 && chmod 0755 /usr/local/bin/phpunit /usr/local/bin/composer \
 && apt-get purge -y curl \
 && rm -rf /var/lib/apt/lists/*
RUN useradd --uid 1000 --create-home runner && install -d -o 1000 -g 1000 /workspace
USER 1000:1000
WORKDIR /workspace
