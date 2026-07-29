#!/usr/bin/env bash
set -euo pipefail

: "${GITHUB_WORKSPACE:?GITHUB_WORKSPACE is required}"
: "${RUNNER_TEMP:?RUNNER_TEMP is required}"
: "${RUNNER_WORKSPACE:?RUNNER_WORKSPACE is required}"

real_claude="${AI_LEAN_REAL_CLAUDE:-$HOME/.local/bin/claude}"
if [[ ! -x "$real_claude" ]]; then
  echo "::error::Claude Code executable not found at $real_claude" >&2
  exit 1
fi
if ! command -v bwrap >/dev/null 2>&1; then
  echo "::error::bubblewrap is not installed" >&2
  exit 1
fi

workspace="$(realpath "$GITHUB_WORKSPACE")"
runner_temp="$(realpath "$RUNNER_TEMP")"
actions_root="$(realpath -m "$RUNNER_WORKSPACE/../_actions")"
sandbox_home="$runner_temp/ai-lean-claude-home"
mkdir -p "$sandbox_home"

if [[ ! -e "$workspace/.git" ]]; then
  echo "::error::$workspace/.git does not exist; cannot make Git metadata read-only" >&2
  exit 1
fi

# The Claude process needs its provider credential, but no GitHub, Actions,
# cache, OIDC, or caller-defined environment reaches it. The read-only root
# supplies the runner toolchain. Narrow overlays then grant workspace/temp
# writes, hide downloaded action sources, and make Git metadata immutable.
#
# Order matters. bwrap applies mounts in sequence and its binds are recursive,
# so `--ro-bind / /` must come first: placing it after `--proc`/`--dev` would
# bind the host's procfs over the sandbox's own, and `/proc` reports the
# namespace of whoever mounted it -- host processes would stay visible and
# --unshare-pid would buy nothing.
bwrap_args=(
  --die-with-parent
  --new-session
  --unshare-pid
  --ro-bind / /
  --proc /proc
  --dev /dev
  --tmpfs /tmp
  --bind "$workspace" "$workspace"
  --ro-bind "$workspace/.git" "$workspace/.git"
  --bind "$runner_temp" "$runner_temp"
  --tmpfs "$actions_root"
  --chdir "$workspace"
  --clearenv
  --setenv HOME "$sandbox_home"
  --setenv PATH "$HOME/.local/bin:$HOME/.elan/bin:/usr/local/bin:/usr/bin:/bin"
  # HOME is redirected to a scratch directory, so elan can no longer find its
  # toolchains at the default $HOME/.elan. Without this, every `lake` the agent
  # runs through run-lean-sanitized.sh fails to resolve a toolchain.
  --setenv ELAN_HOME "$HOME/.elan"
  --setenv GIT_OPTIONAL_LOCKS "0"
  --setenv CI "true"
  --setenv GITHUB_ACTIONS "true"
)

pass_if_set() {
  local name="$1"
  if [[ -n "${!name:-}" ]]; then
    bwrap_args+=(--setenv "$name" "${!name}")
  fi
}

# Exactly one of these is normally set by the base action. They are required
# for Claude itself and are the only secrets deliberately passed through.
# --clearenv means anything absent from this list is dropped silently, so when
# the base-action pin moves, extend the list here rather than debugging an
# opaque CLI failure.
pass_if_set ANTHROPIC_API_KEY
pass_if_set CLAUDE_CODE_OAUTH_TOKEN
pass_if_set ANTHROPIC_BASE_URL
pass_if_set ANTHROPIC_CUSTOM_HEADERS
pass_if_set CLAUDE_CODE_ACTION

# Locale and terminal hints are non-secret and avoid avoidable CLI issues.
pass_if_set LANG
pass_if_set LC_ALL
pass_if_set TERM

# Preserve the former base-action timeout while ensuring the whole sandbox is
# torn down if Claude or one of its children does not exit.
exec timeout --foreground --kill-after=10 1800 \
  bwrap "${bwrap_args[@]}" "$real_claude" "$@"
