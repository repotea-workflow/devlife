ARG K8S_VERSION
ARG HELMFILE_VERSION


##
# Helmfile
##
FROM quay.io/roboll/helmfile:helm3-${HELMFILE_VERSION} as helmfile


##
# MAIN STAGE
##
FROM alpine/k8s:${K8S_VERSION}

# Helmfile
COPY --from=helmfile /usr/local/bin/helmfile /usr/local/bin/

RUN apk update && apk add --no-cache ca-certificates curl make colordiff moreutils bash git
