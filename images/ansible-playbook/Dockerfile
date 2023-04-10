FROM python:3.9-slim-buster

RUN set -xe \
    && echo 'deb http://deb.debian.org/debian buster-backports main' > /etc/apt/sources.list.d/backports.list \
    && apt-get update && apt-get install -y --no-install-recommends \
        openssh-client \
        git \
    && rm -rf /var/lib/apt/lists/* \
    && pip3 --no-cache-dir install ansible netaddr dnspython

