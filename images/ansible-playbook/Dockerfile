FROM python:3.11-slim

RUN set -xe \
    && apt-get update -y \
    && apt-get install -y --no-install-recommends \
        openssh-client \
        git \
    && rm -rf /var/lib/apt/lists/* \
    && pip3 --no-cache-dir install ansible netaddr dnspython

