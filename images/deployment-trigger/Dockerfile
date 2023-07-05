FROM buildpack-deps:stable-curl as yq
ARG YQ_VERSION
RUN curl -LO "https://github.com/mikefarah/yq/releases/download/${YQ_VERSION}/yq_linux_amd64"
RUN chmod +x yq_linux_amd64

FROM buildpack-deps:stable-curl as kustomize
ARG KUSTOMIZE_VERSION
RUN curl -LO "https://github.com/kubernetes-sigs/kustomize/releases/download/kustomize%2F${KUSTOMIZE_VERSION}/kustomize_${KUSTOMIZE_VERSION}_linux_amd64.tar.gz"
RUN tar xzf ./kustomize_v*_linux_amd64.tar.gz

##
# MAIN STAGE
##

FROM alpine:3

RUN apk add --no-cache \
    git openssh-client colordiff nodejs npm jq

COPY --from=yq ./yq_linux_amd64 /usr/local/bin/yq
COPY --from=kustomize ./kustomize /usr/local/bin/
RUN npm install -g prettier
