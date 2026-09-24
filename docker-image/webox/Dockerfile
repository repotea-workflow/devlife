# syntax=docker/dockerfile:1
# Fetch netcat-ai/webox at a pinned commit and bundle the WeChat package.

ARG WEBOX_REF=8ca042cd79221d05f6a74cb48064c7ba14220dc9
ARG WECHAT_DEB_URL=https://dldir1v6.qq.com/weixin/Universal/Linux/WeChatLinux_x86_64.deb
ARG WECHAT_DEB_SHA256=b7d0f8d53e9f648bc2c77a6096a04100d008f2d9f0d3988a2a4859b5992aca0a
ARG GO_BUILDER_IMAGE=golang:1.26-bookworm
ARG DEBIAN_RUNTIME_IMAGE=debian:bookworm-slim

FROM alpine:3.21 AS src
RUN apk add --no-cache git ca-certificates curl
WORKDIR /src
ARG WEBOX_REF
ARG WECHAT_DEB_URL
ARG WECHAT_DEB_SHA256
RUN git init \
  && git remote add origin https://github.com/netcat-ai/webox.git \
  && git fetch --depth 1 origin "${WEBOX_REF}" \
  && git checkout --detach FETCH_HEAD \
  && mkdir -p docker/wechat \
  && curl --fail --location --retry 5 --retry-all-errors --output docker/wechat/WeChatLinux_x86_64.deb "$WECHAT_DEB_URL" \
  && echo "${WECHAT_DEB_SHA256}  docker/wechat/WeChatLinux_x86_64.deb" | sha256sum -c -

FROM ${GO_BUILDER_IMAGE} AS build
WORKDIR /src
COPY --from=src /src/go.mod /src/go.sum ./
RUN --mount=type=cache,target=/go/pkg/mod go mod download
COPY --from=src /src/cmd ./cmd
COPY --from=src /src/internal ./internal
COPY --from=src /src/wecom ./wecom
RUN --mount=type=cache,target=/go/pkg/mod \
    --mount=type=cache,target=/root/.cache/go-build \
    mkdir -p /out && \
    CGO_ENABLED=1 go build -trimpath -ldflags="-s -w -extldflags=-s" -o /out/weagent ./cmd/weagent

FROM ${DEBIAN_RUNTIME_IMAGE} AS novnc-assets
RUN apt-get update && \
    apt-get install -y --no-install-recommends novnc && \
    mkdir -p /out && cp -a /usr/share/novnc/. /out/

FROM ${DEBIAN_RUNTIME_IMAGE} AS runtime-base
ARG APT_DEBIAN_MIRROR=
ARG APT_DEBIAN_SECURITY_MIRROR=

ENV DEBIAN_FRONTEND=noninteractive \
    LANG=zh_CN.UTF-8 \
    LC_ALL=zh_CN.UTF-8 \
    LIBGL_ALWAYS_SOFTWARE=1 \
    LD_LIBRARY_PATH=/webox/weagent/lib

RUN set -eux; \
    if [ -n "$APT_DEBIAN_MIRROR" ] && [ -f /etc/apt/sources.list.d/debian.sources ]; then \
      sed -i "s|http://deb.debian.org/debian|$APT_DEBIAN_MIRROR|g" /etc/apt/sources.list.d/debian.sources; \
    fi; \
    if [ -n "$APT_DEBIAN_SECURITY_MIRROR" ] && [ -f /etc/apt/sources.list.d/debian.sources ]; then \
      sed -i "s|http://deb.debian.org/debian-security|$APT_DEBIAN_SECURITY_MIRROR|g; s|http://security.debian.org/debian-security|$APT_DEBIAN_SECURITY_MIRROR|g" /etc/apt/sources.list.d/debian.sources; \
    fi; \
    apt-get update; \
    apt-get install -y --no-install-recommends \
      ca-certificates dbus dpkg ffmpeg gosu libcap2-bin locales \
      openbox tini websockify x11vnc xclip xdotool xsettingsd xvfb \
      fonts-wqy-microhei fonts-noto-color-emoji \
      libatomic1 libnss3 libgbm1 libasound2 libpulse0 libxss1 libxdamage1 libxkbcommon-x11-0 \
      libxcb-icccm4 libxcb-image0 libxcb-keysyms1 libxcb-render-util0 libxcb-xkb1 libxcb-cursor0 \
      libgtk-3-0 libatk1.0-0 libatk-bridge2.0-0 libatspi2.0-0 libcups2 \
      libxcomposite1 libxrandr2 libxfixes3 libxtst6 libxshmfence1 libdrm2; \
    sed -i 's/# zh_CN.UTF-8 UTF-8/zh_CN.UTF-8 UTF-8/' /etc/locale.gen; \
    locale-gen; \
    useradd -M -d /webox/state/home -u 1000 -s /bin/bash webox; \
    mkdir -p /webox/wechat /webox/weagent/bin /webox/state /webox/runtime; \
    chown -R webox:webox /webox; \
    apt-get clean; \
    rm -rf \
      /var/lib/apt/lists/* \
      /usr/share/doc/* \
      /usr/share/man/* \
      /usr/share/locale/*

COPY --from=novnc-assets /out/ /usr/share/novnc/

FROM runtime-base AS runtime-wechat

COPY --from=src /src/docker/wechat /tmp/wechat
RUN set -eux; \
    arch="$(dpkg --print-architecture)"; \
    case "$arch" in \
      amd64) deb="/tmp/wechat/WeChatLinux_x86_64.deb" ;; \
      arm64) deb="/tmp/wechat/WeChatLinux_arm64.deb" ;; \
      *) echo "unsupported architecture for bundled WeChat: $arch" >&2; exit 1 ;; \
    esac; \
    if [ ! -f "$deb" ]; then \
      echo "missing bundled WeChat package: $deb" >&2; \
      echo "put WeChatLinux_x86_64.deb or WeChatLinux_arm64.deb under docker/wechat before building" >&2; \
      exit 1; \
    fi; \
    dpkg-deb -x "$deb" /webox/wechat; \
    dpkg-deb -f "$deb" Version > /webox/wechat/.webox-version; \
    test -x /webox/wechat/opt/wechat/wechat

FROM runtime-wechat AS runtime

COPY --from=build /out/weagent /webox/weagent/bin/weagent
COPY --from=src /src/docker/scripts/webox-identity.sh /src/docker/scripts/entrypoint.sh /webox/weagent/bin/

RUN chmod 755 /webox/weagent/bin/weagent /webox/weagent/bin/*.sh && \
    setcap cap_sys_ptrace=ep /webox/weagent/bin/weagent

VOLUME ["/webox/state"]
EXPOSE 8080 6080

ENTRYPOINT ["/usr/bin/tini", "--", "/webox/weagent/bin/entrypoint.sh"]
