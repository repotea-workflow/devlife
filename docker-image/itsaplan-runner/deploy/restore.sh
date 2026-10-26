#!/bin/sh
# Invoke after the existing Docker daemon is ready; this is not a Runner daemon.
set -eu
cd /data/itsaplan/runner/deploy
test -s .env
docker info >/dev/null
apparmor_parser -r apparmor.profile
exec docker compose --env-file .env -f compose.yaml up -d
