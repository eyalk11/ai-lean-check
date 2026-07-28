# AI Lean Check

`ai-lean-check` is a GitHub composite action that asks Claude Code or Codex to
generate a focused Lean verification file and then independently compiles it.
The generated file is an artifact; the agent cannot modify committed project
files.

## What it does

1. Installs the toolchain from `lean-toolchain` and runs `lake build`.
2. Rejects `sorry` or `admit` outside designated dependency files.
3. Collects the changed Lean source plus configured context.
4. Gives Claude Code or Codex a constrained task to create one generated file.
5. Lets the agent run only a credential-scrubbing Lean wrapper.
6. Confirms that `HEAD` and all tracked files are unchanged.
7. Rejects unsafe escape hatches in the generated source.
8. Runs `lake env lean` on the generated file and reruns `lake build`.
9. Uploads the generated file and diagnostics as `ai-lean-check`.

## Claude Code with a GitHub environment

GitHub environments are selected on the caller's **job**, not inside a
composite action. If an environment named `main` contains an API-key secret
named `CLAUDE_CODE_KEY`, use:

```yaml
name: AI Lean Check

on:
  pull_request:
    paths:
      - "**/*.lean"
      - "lakefile.toml"
      - "lean-toolchain"
  workflow_dispatch:

permissions:
  contents: read

jobs:
  lean-ai:
    runs-on: ubuntu-latest
    environment: main
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 2
      - uses: eyalk11/ai-lean-check@main
        with:
          provider: claude-code
          source-paths: |
            **/*.lean
          context-files: |
            lakefile.toml
            lean-toolchain
          deps-sorry-policy: reject
        env:
          ANTHROPIC_API_KEY: ${{ secrets.CLAUDE_CODE_KEY }}
```

`CLAUDE_CODE_KEY` is the repository/environment secret's name. It is mapped to
`ANTHROPIC_API_KEY` because that is the variable consumed by the upstream
Claude Code action. For a long-lived Claude Code OAuth credential instead:

```yaml
        env:
          CLAUDE_CODE_OAUTH_TOKEN: ${{ secrets.CLAUDE_CODE_OAUTH_TOKEN }}
```

The environment may require approval before the job starts, depending on its
GitHub protection rules.

## Codex

```yaml
      - uses: eyalk11/ai-lean-check@main
        with:
          provider: codex
          imports: |
            MyProject
          task: Generate examples that exercise the declarations changed by this PR.
        env:
          OPENAI_API_KEY: ${{ secrets.OPENAI_API_KEY }}
```

## Configuration

All composite-action inputs are strings. Write booleans as `"true"` or
`"false"`.

| Input | Default | Meaning |
|---|---|---|
| `provider` | `claude-code` | Required mode: `claude-code` or `codex`; every other value fails |
| `model` | Claude: `claude-sonnet-4-6`; Codex: upstream default | Optional explicit model ID |
| `source-paths` | `*.lean`, `**/*.lean` | Newline-separated Git pathspecs used for diff collection and placeholder scanning |
| `context-files` | empty | Newline-separated globs for additional read-only prompt context |
| `imports` | empty | Newline-separated modules the generated file must import |
| `task` | Generate meaningful compile-time checks | Extra generation instructions |
| `agent-max-turns` | `20` | Maximum Claude Code turns; ignored by Codex |
| `output-file` | `.ai-lean-check/GeneratedCheck.lean` | Generated file path |
| `verification-command` | empty | Additional shell verification run after mandatory checks with provider and GitHub credentials removed |
| `setup-lean` | `"true"` | Run `leanprover/lean-action@v1` |
| `build-project` | `"true"` | Run the initial `lake build` when Lean setup is enabled |
| `use-mathlib-cache` | `auto` | Passed to `leanprover/lean-action` |
| `upload-artifact` | `"true"` | Upload generated source, prompt, and diagnostics |
| `base-sha` | PR base or `HEAD^` | Explicit base revision for the Lean diff |
| `head-sha` | PR head or `HEAD` | Explicit head revision for the Lean diff |
| `max-context-bytes` | `200000` | Legacy context cap used only if `max-input-tokens` is empty |
| `max-input-tokens` | `50000` | Approximate prompt-context cap, estimated conservatively at four UTF-8 bytes per token |
| `max-output-tokens` | `32768` | Reserved for compatibility; agent actions control their own output |
| `max-repair-attempts` | `2` | Reserved for compatibility; coding agents repair within their own turns |
| `deps-sorry-policy` | `warn` | `warn` or `reject` for placeholders inside designated dependency files |
| `sorry-allowed-files` | `**/*_deps.lean` | Newline-separated dependency-file globs |

The action always fails when `sorry` or `admit` occurs outside a file matched by
`sorry-allowed-files`. Inside those files, `deps-sorry-policy: warn` emits an
annotation and continues; `reject` fails.

Suggested dependency naming:

```text
Lean/theorem_3_11_deps.lean
Lean/theorem_3_11.lean
```

## Additional Lean setup steps

Add arbitrary setup steps to the caller workflow before `ai-lean-check`. If
those steps install the toolchain and build the project, set `setup-lean:
"false"` so the composite action does not repeat that work:

```yaml
      - uses: actions/checkout@v4
      - name: Project-specific setup
        run: ./scripts/prepare-lean-project.sh
      - uses: eyalk11/ai-lean-check@main
        with:
          provider: claude-code
          setup-lean: "false"
        env:
          ANTHROPIC_API_KEY: ${{ secrets.CLAUDE_CODE_KEY }}
```

This deliberately keeps arbitrary shell commands in the visible caller
workflow instead of accepting an opaque command string as an action input.

## Credentials and isolation

The caller passes exactly one provider credential:

| Provider | Caller environment variable |
|---|---|
| Claude Code API key | `ANTHROPIC_API_KEY` |
| Claude Code OAuth | `CLAUDE_CODE_OAUTH_TOKEN` |
| Codex | `OPENAI_API_KEY` |

The agent steps explicitly clear `GITHUB_TOKEN` and `GH_TOKEN`. The generated
Lean wrapper clears provider credentials, GitHub tokens, Actions runtime
tokens, and OIDC request credentials before invoking Lean. The action also
removes the checkout token persisted in the repository's Git HTTP configuration
before the agent starts. Claude Code gets
only read/edit tools plus that wrapper; Codex uses a workspace-write sandbox
with sudo disabled. After the agent finishes, an independent verifier rejects
any tracked-file or commit change.

The optional `verification-command` runs last, after `lake env lean
<output-file>` and `lake build`. It receives the same scrubbed environment and
cannot reuse the checkout authorization header:

```yaml
        with:
          verification-command: |
            lake test
            ./scripts/check-generated-proof.sh
```

Repository secrets are not provided to workflows triggered from untrusted
forks. Use an environment approval rule, restrict the job to trusted branches,
or skip agent jobs for forked pull requests.

## Failure and branch protection

The job fails when:

- the provider is not `claude-code` or `codex`;
- credentials are absent or invalid;
- the initial project build fails;
- ordinary Lean files contain `sorry` or `admit`;
- dependency placeholders violate `deps-sorry-policy`;
- the agent changes tracked files or `HEAD`;
- generated code uses a forbidden construct;
- the generated file or final `lake build` does not compile.

Repository administrators can bypass a required check only if the branch rules
or ruleset permits bypass. GitHub can be configured to forbid administrator
bypass.

## Local checks

```bash
python -m unittest discover -s tests -v
lake build
```
