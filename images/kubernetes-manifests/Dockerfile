ARG KUBECTL_VERSION
ARG HELMFILE_VERSION

##
# Kubectl
##
FROM bitnami/kubectl:${KUBECTL_VERSION} as kubectl

##
# Kustomize
##
FROM buildpack-deps:stable-curl as kustomize
ARG KUSTOMIZE_VERSION
RUN curl -LO "https://github.com/kubernetes-sigs/kustomize/releases/download/kustomize%2F${KUSTOMIZE_VERSION}/kustomize_${KUSTOMIZE_VERSION}_linux_amd64.tar.gz"
RUN tar xzvf ./kustomize_v*_linux_amd64.tar.gz

##
# Helm
##
FROM buildpack-deps:stable-curl as helm
ARG HELM_VERSION
ARG HELM_FILENAME="helm-${HELM_VERSION}-linux-amd64.tar.gz"
RUN wget "https://get.helm.sh/${HELM_FILENAME}"
RUN tar zxvf ./${HELM_FILENAME}

##
# Helmfile
##
FROM quay.io/roboll/helmfile:${HELMFILE_VERSION} as helmfile

##
# MAIN STAGE
##
FROM alpine:3

# Kubectl
COPY --from=kubectl /opt/bitnami/kubectl/bin/kubectl /usr/local/bin/
# Kustomize
COPY --from=kustomize ./kustomize /usr/local/bin/
# Helm
COPY --from=helm ./linux-amd64/helm /usr/local/bin/
# Helmfile
COPY --from=helmfile /usr/local/bin/helmfile /usr/local/bin/

# Other deps
RUN apk add --no-cache ca-certificates curl make colordiff moreutils bash git \
    && helm plugin install https://github.com/databus23/helm-diff --version v3.1.3
