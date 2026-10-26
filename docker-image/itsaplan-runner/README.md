# Itsaplan Runner

One official `@itsaplan/runner` process per machine, containing Codex, Pi, Grok
Build, Cursor CLI, Git/GitHub tools and the itsaplan API CLI. The official upstream
image workflow publishes api/worker/bot/web; it does not publish a Runner image.
Birch currently uses the Kangumi dev-toolchain developer image, but its SSH/s6
entrypoint and runtime Homebrew installation are not required in this image.

Build from this directory with Docker BuildKit. Versions are pinned in
`package-lock.json` and Dockerfile; native downloads are SHA256-verified. The
current native Grok/Cursor checksums support Linux amd64 only. The existing
repository workflow can publish package `itsaplan-runner` to
`ghcr.io/repotea-workflow/devlife/itsaplan-runner:sha-<commit>`; deploy its verified
registry digest, never `latest`. No credential, host config, native host binary,
profile or runtime state belongs in the build context.

## Persistent state and credentials

- `/code` is mounted at exactly `/code`, preserving linked worktree metadata and
  includeIf routing. The project registry still selects all repositories for a
  task. Branch naming remains `<git-user>/<project-lower>-<original-run-id>`.
- `/data/itsaplan` contains profiles, archives, isolated Runner state and launch
  definitions. Runner home is `/data/itsaplan/runner/home`; native tool homes are
  `/data/itsaplan/runner/homes/{codex,pi,grok,cursor}`. Profiles remain separated by
  engine in `/data/itsaplan/agents/<engine>`, shared by Agents using that engine.
- `deploy/prepare-state.py` makes a one-time copy of a **stopped** existing Runner
  state and CLI contexts, preserving platform keys, provider settings, model
  arguments, concurrency and team sharing. It refuses an existing destination.
  The new state mounts at the old absolute path only inside the container; the
  daily host CLI remains separate. All state/credential directories need UID/GID
  `0:0` and mode `0700`; credential files need `0600`.
- Only the existing Git authority paths are mounted read-only from the host,
  including GitHub account and SSH credentials. Standalone `gh` resolves
  `GH_CONFIG_DIR` from the project's canonical Git credential helper. The image
  supplies the helper's historical Homebrew `gh` path without altering includeIf.
  The existing Pragmatic package directory is mounted read-only because Codex
  marketplace configuration and AGENTS.md refer to its absolute location.
- Machine-specific `runner.json` environment overrides retain cleanup,
  collaboration and registry paths. Do not replace Birch's COSMOS/AVIARY settings
  with Aspen's DGV values. Models belong in each Agent's command/args.

## Sandbox and launch

Load `deploy/apparmor.profile` as `itsaplan-runner` in the Docker daemon's AppArmor
namespace using `apparmor_parser -r`. Retain that source and load it at machine
startup **before** the Runner starts. `deploy/seccomp.json` derives from Moby
profiles commit `2ceae35d351c156cb5a8efc0fdc4a08cf94569d8`; only clone/clone3,
unshare, setns, mount, umount/umount2 and pivot_root are additionally allowed for
the coding agents' inner namespaces. Existing capabilities are retained; no
privileged mode or added host SYS_ADMIN capability is used.

Docker masked proc mounts prevent bubblewrap from mounting proc in its new PID
namespace. `systempaths=unconfined` removes these per-container mount masks;
AppArmor explicitly denies writes to proc/sys and sys and denies access to the
sensitive paths formerly masked by Docker. Keep this profile and seccomp together.
Do not disable the agents' inner sandbox to bypass a failed host prerequisite.
Verify `codex sandbox -- /bin/sh -c 'printf SANDBOX_OK'` under the final options.

Copy the reviewed deploy files into `/data/itsaplan/runner/deploy`, set
`ITSAPLAN_RUNNER_IMAGE` to the verified immutable image ID/digest, and run
`docker compose --env-file .env -f compose.yaml up -d` from that directory.
The env file contains only the image reference. Logs rotate under Docker.
The entrypoint holds a persistent `flock` for the official Runner process.
Tini forwards SIGTERM to that process; the official Runner stops claiming work
and drains active tasks. The 24-hour stop grace exceeds the configured task
timeout. Never send a second signal or force-stop an active migration drain.
`unless-stopped` survives daemon restarts; a deliberately stopped container needs
an explicit start.

Birch's daemon runs inside its Kubernetes developer container. Its image cache
and containers persist in `/var/lib/docker`, but AppArmor loading and daemon
startup must also survive recreation of that outer container. Add the reviewed
idempotent Docker restore invocation to the existing developer-container startup
definition; do not retain an old s6/systemd **Runner** consumer. Verify a controlled
container recreation rather than assuming one successful `docker run` proves it.

## Acceptance and migration

Before copying state, confirm no active Runner tasks, then disable/drain the old
machine-specific Runner unit or s6 entry. Keep profiles, archives, source trees,
branches and task evidence. Deploy only one Docker Runner, verify all Agent
heartbeats, and resume existing queued runs without creating new duplicates.
Delete only the identified obsolete Runner-specific service definitions after
the new deployment passes. Leave Docker, SSH and other machine services intact.

The image build runs native Git lifecycle/finalize tests and four CLI version
checks. Runtime acceptance also needs task-scoped write/read-only source proofs,
Git identity and authenticated GitHub access, real team-member task execution,
independent review dependencies, Agent-owned platform state/finalize and verified
archive recovery, graceful stop/start, single consumer and persisted recreation.
An Agent must explicitly request finalize; no background cleanup is installed.
Retained control workspaces and source checkouts must stay unchanged.

Aspen's persistent Docker service drop-in `deploy/docker-apparmor.conf` loads the
profile before daemon-managed container restoration. It does not launch a Runner
outside Docker. Birch must call `deploy/restore.sh` only after its existing DinD
daemon is ready; its AppArmor namespace needs permission to load this exact
profile. If that host prerequisite fails, stop and report it rather than bypassing
the profile. Do not synchronously wait for dockerd from an outer container init
script that itself prevents the Docker service from starting.

## Agent execution policy

The user-approved Runner policy is unrestricted unattended execution. Codex uses
`sandbox_mode="danger-full-access"` and `approval_policy="never"`; Cursor uses
`--sandbox disabled --force --trust --approve-mcps`; Grok uses `--always-approve`
and Pi uses `--approve`. The adapter does not inject a read-only Codex permission
profile or wrap Cursor in bubblewrap. Engine profiles and model arguments remain
unchanged. Routine Flutter/FVM and other tool cache initialization must be writable.

Keep the existing task worktrees, Git identity routing, submodule metadata integrity,
independent review, delivery acceptance and guarded finalization. Full tool access
must not overwrite unrelated work or expose credentials. Docker deployment options
are container configuration, not a task filesystem permission profile; check any
read-only SDK mounts if a required tool still fails. No new service is introduced.
