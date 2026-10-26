# syntax=docker/dockerfile:1
FROM node:24-trixie-slim@sha256:173f125896c3b47ddf056734c7ea789d04595a6a08769a8f78e0df642781fb66

ARG TARGETARCH=amd64
ARG GROK_VERSION=1.0.46
ARG GROK_SHA256=41626a53292324140b92556b9d42ff5542e3dcd04aff85eafb8689dd4adb44fc
ARG CURSOR_VERSION=2026.10.01-e373342
ARG CURSOR_SHA256=a79726c6e644520e993970be4c45775a6889802b67abe461a677a53219ae28e8
ARG ITSAPLAN_CLI_VERSION=0.7.1
ARG ITSAPLAN_CLI_SHA256=5adc3fada8bf2e7974c27a5e967bae8a53df1666645074fee4858ef8b9c1db43

RUN --mount=type=tmpfs,target=/var/lib/apt/lists test "$TARGETARCH" = amd64 && apt-get update && apt-get install -y --no-install-recommends \
    bash bubblewrap build-essential ca-certificates curl gh git gnupg iproute2 jq \
    libcap2-bin openssh-client procps python3 ripgrep tini unzip util-linux

WORKDIR /opt/itsaplan-runner
COPY package.json package-lock.json ./
RUN --mount=type=cache,target=/root/.npm npm ci --no-audit --no-fund && mkdir -p bin adapters /opt/shims /opt/cursor-agent \
    /home/linuxbrew/.linuxbrew/bin && \
    ln -s /opt/itsaplan-runner/node_modules/.bin/pi /usr/local/bin/pi && \
    ln -s /opt/itsaplan-runner/node_modules/.bin/codex bin/codex && \
    ln -s /usr/bin/gh bin/gh && \
    ln -s /usr/bin/gh /home/linuxbrew/.linuxbrew/bin/gh

RUN --mount=type=tmpfs,target=/tmp curl -fsSL --retry 3 "https://x.ai/cli/grok-${GROK_VERSION}-linux-x86_64" -o bin/grok && \
    echo "${GROK_SHA256}  bin/grok" | sha256sum -c - && chmod 755 bin/grok && \
    curl -fsSL --retry 3 "https://downloads.cursor.com/lab/${CURSOR_VERSION}/linux/x64/agent-cli-package.tar.gz" -o /tmp/cursor.tar.gz && \
    echo "${CURSOR_SHA256}  /tmp/cursor.tar.gz" | sha256sum -c - && \
    tar -xzf /tmp/cursor.tar.gz --strip-components=1 -C /opt/cursor-agent && \
    ln -s /opt/cursor-agent/cursor-agent bin/cursor-agent && \
    curl -fsSL --retry 3 "https://github.com/0xfe10/aicli/releases/download/v${ITSAPLAN_CLI_VERSION}/itsaplan_${ITSAPLAN_CLI_VERSION}_linux_amd64.tar.gz" -o /tmp/itsaplan.tar.gz && \
    echo "${ITSAPLAN_CLI_SHA256}  /tmp/itsaplan.tar.gz" | sha256sum -c - && \
    tar -xzf /tmp/itsaplan.tar.gz -C /opt/shims itsaplan

COPY runtime/ ./
COPY entrypoint.sh /usr/local/bin/itsaplan-runner-entrypoint
COPY finalize.sh /opt/shims/itsaplan-finalize
RUN --mount=type=tmpfs,target=/tmp chmod 755 /usr/local/bin/itsaplan-runner-entrypoint /opt/shims/itsaplan-finalize \
    bin/external-command adapters/codex adapters/pi && \
    ln -s /opt/shims/itsaplan /usr/local/bin/itsaplan && \
    ln -s /opt/shims/itsaplan-finalize /usr/local/bin/itsaplan-finalize && \
    PYTHONPATH=/opt/itsaplan-runner/bin python3 -m unittest discover -s tests && \
    bin/codex --version && /usr/local/bin/pi --version && bin/grok --version && \
    bin/cursor-agent --version && /opt/shims/itsaplan --version

ENV HOME=/data/itsaplan/runner/home \
    XDG_CONFIG_HOME=/data/itsaplan/runner/home/.config \
    XDG_CACHE_HOME=/data/itsaplan/runner/home/.cache \
    ITSAPLAN_RUNNER_CONFIG=/root/.config/itsaplan-runner/runner.json \
    PATH=/opt/itsaplan-runner/adapters:/opt/itsaplan-runner/bin:/opt/shims:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin

STOPSIGNAL SIGTERM
ENTRYPOINT ["/usr/bin/tini", "--", "/usr/local/bin/itsaplan-runner-entrypoint"]
