
FROM debian:bullseye
ARG TERRAFORM_VERSION
ARG TERRAFORM_BACKEND_GIT_VERSION

RUN set -xe \
  && apt-get -y update \
  && apt-get -y install curl unzip jq \
  && curl -L -o /tmp/terraform.zip "https://releases.hashicorp.com/terraform/${TERRAFORM_VERSION}/terraform_${TERRAFORM_VERSION}_linux_amd64.zip" \
  && unzip -d /bin /tmp/terraform.zip \
  && curl -L -o /bin/terraform-backend-git "https://github.com/plumber-cd/terraform-backend-git/releases/download/${TERRAFORM_BACKEND_GIT_VERSION}/terraform-backend-git-linux-amd64" \
  && chmod +x /bin/terraform-backend-git
