#!/bin/sh
set -eu
umask 077
mkdir -p "$HOME" "$XDG_CONFIG_HOME" "$XDG_CACHE_HOME" /data/itsaplan/runner/locks
test -r "$ITSAPLAN_RUNNER_CONFIG"
# Keep the lock in the Runner process. SIGTERM reaches only the Runner first,
# allowing the official implementation to drain its active commands.
exec flock --exclusive --nonblock --no-fork /data/itsaplan/runner/locks/consumer.lock \
    node /opt/itsaplan-runner/node_modules/@itsaplan/runner/dist/cli.js "$ITSAPLAN_RUNNER_CONFIG" "$@"
