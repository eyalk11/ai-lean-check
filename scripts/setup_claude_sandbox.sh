#!/usr/bin/env bash
set -euo pipefail

if [[ "$(uname -s)" != Linux ]]; then
  echo "::error::The Claude sandbox requires a Linux runner." >&2
  exit 1
fi

chmod 700 "$GITHUB_ACTION_PATH/scripts/claude-bwrap.sh"

if ! command -v bwrap >/dev/null 2>&1; then
  if ! command -v apt-get >/dev/null 2>&1; then
    echo "::error::bubblewrap is missing and apt-get is unavailable." >&2
    exit 1
  fi
  sudo apt-get update
  sudo apt-get install --yes --no-install-recommends bubblewrap
fi

# Exercise the exact primitive now so the job fails before downloading or
# invoking Claude. Counting the PIDs visible inside the sandbox also proves the
# isolation actually holds: a successful exit alone would still pass with the
# host's procfs bound over the sandbox's own. --clearenv is exercised
# deliberately too, because bubblewrap before 0.5 does not have it and would
# otherwise fail partway into a paid agent run instead of here.
probe_err="${RUNNER_TEMP:-/tmp}/bwrap-probe.err"
probe_sandbox() {
  bwrap --die-with-parent --new-session --unshare-pid \
    --ro-bind / / --proc /proc --dev /dev --tmpfs /tmp \
    --clearenv --setenv PATH /usr/bin:/bin \
    sh -c 'ls /proc | grep -c "^[0-9]*$"' 2>"$probe_err"
}

visible_pids="$(probe_sandbox || true)"

# Ubuntu 24.04 blocks unprivileged user namespaces through AppArmor, which is
# what a GitHub ubuntu-latest runner now is. The restriction is a sysctl and
# the runner has passwordless sudo, so relax it and retry rather than making
# the sandbox unavailable on the default runner image.
restrict_sysctl=/proc/sys/kernel/apparmor_restrict_unprivileged_userns
if [[ -z "$visible_pids" && "$(cat "$restrict_sysctl" 2>/dev/null || echo 0)" == "1" ]] \
   && command -v sudo >/dev/null 2>&1; then
  echo "::notice::relaxing kernel.apparmor_restrict_unprivileged_userns for bubblewrap"
  sudo sysctl --quiet --write kernel.apparmor_restrict_unprivileged_userns=0 || true
  visible_pids="$(probe_sandbox || true)"
fi

if [[ -z "$visible_pids" ]]; then
  echo "::error::bubblewrap cannot create an unprivileged sandbox on this runner." >&2
  echo "bwrap reported:" >&2
  cat "$probe_err" >&2 || true
  exit 1
fi
if (( visible_pids > 10 )); then
  echo "::error::sandbox /proc shows $visible_pids processes; PID isolation is not in effect" >&2
  exit 1
fi

claude_version="${AI_LEAN_CLAUDE_CODE_VERSION:-2.1.220}"
claude_bin="$HOME/.local/bin/claude"
if [[ ! -x "$claude_bin" ]] || [[ "$("$claude_bin" --version 2>/dev/null || true)" != *"$claude_version"* ]]; then
  installer="$RUNNER_TEMP/install-claude.sh"
  curl --fail --silent --show-error --location \
    https://claude.ai/install.sh --output "$installer"
  bash "$installer" "$claude_version"
  rm -f "$installer"
fi

"$claude_bin" --version
