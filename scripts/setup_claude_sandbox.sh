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

# Ubuntu 24.04 can restrict unprivileged user namespaces through AppArmor.
# Exercise the exact primitive now so the job fails with a useful message
# before downloading or invoking Claude. Counting the PIDs visible inside the
# sandbox also proves the isolation actually holds: a successful exit alone
# would still pass with the host's procfs bound over the sandbox's own.
# --clearenv is exercised here deliberately: bubblewrap before 0.5 does not
# have it, and without it in the preflight an old bwrap passes this check and
# then fails inside claude-bwrap.sh partway into a paid agent run.
visible_pids="$(bwrap --die-with-parent --new-session --unshare-pid \
  --ro-bind / / --proc /proc --dev /dev --tmpfs /tmp \
  --clearenv --setenv PATH /usr/bin:/bin \
  sh -c 'ls /proc | grep -c "^[0-9]*$"' 2>/dev/null || true)"
if [[ -z "$visible_pids" ]]; then
  cat >&2 <<'EOF'
::error::bubblewrap cannot create an unprivileged sandbox on this runner.
Check kernel.apparmor_restrict_unprivileged_userns and the runner's AppArmor policy.
EOF
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
