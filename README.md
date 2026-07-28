# AI Lean Check

`ai-lean-check` is a generic GitHub composite action that turns Lean changes in
a pull request into an additional machine-checked test.

The generated file is temporary and uploaded with its final diagnostics as the
`ai-lean-check` workflow artifact.

## What it does, step by step

1. **Checks out a complete Git history.** The caller checks out the pull
   request with `fetch-depth: 0`, allowing the action to compare the configured
   base and head commits.

2. **Sets up the caller's Lean project.** Unless `setup-lean: false` is
   selected, the action invokes `leanprover/lean-action@v1`. It installs the
   toolchain declared by `lean-toolchain`, restores the Lake/Mathlib cache, and
   runs `lake build` by default. This establishes a constant baseline that
   already compiles before any AI-generated code is considered.

3. **Checks the assumption-file discipline.** The action scans tracked files
   selected by `source-paths`. A `sorry` or `admit` outside a configured
   dependency file such as `theorem_3_11_deps.lean` always fails. Inside a
   dependency file, `deps-sorry-policy` decides whether assumptions produce
   warnings or failures.

4. **Collects the relevant change.** The action creates a Git diff between the
   PR base and head, limited by `source-paths`. If no matching Lean change is
   present, the job exits successfully without calling a model.

5. **Builds bounded model context.** Files matched by `context-files` are
   appended to the diff. The diff is placed first so it survives truncation.
   `max-input-tokens` limits the approximate context size.

6. **Requests a standalone Lean check.** Direct providers return structured
   JSON containing one complete Lean source file. In `claude-code` or `codex`
   mode, a coding agent reads a generated task file, writes the Lean candidate,
   and may run the permitted Lean commands while iterating.

7. **Validates the generated source before execution.** The action rejects
   generated `sorry`, `admit`, axioms, unsafe declarations, `run_cmd`, `#eval`,
   `#compile`, initializers, foreign declarations, and direct `IO` or `System`
   access. It also verifies every module named by `imports` is imported.

8. **Compiles the candidate with Lean.** Provider credentials are removed from
   the compiler environment, then the action runs:

   ```sh
   lake env lean .ai-lean-check/GeneratedCheck.lean
   ```

9. **Repairs bounded failures.** If validation or compilation fails, the
   diagnostics are sent back to the same model and it must replace the entire
   candidate. `max-repair-attempts` limits this loop. With the default of `2`,
   the action makes at most three model calls: one generation and two repairs.

10. **Publishes an auditable result.** On success, the job returns the generated
    file path and number of attempts. On success or failure, the final candidate
    and diagnostics are uploaded as the `ai-lean-check` artifact when
    `upload-artifact: true`. The action never commits generated code.

## Usage

GitHub Models is the default provider. It uses the workflow's temporary
`GITHUB_TOKEN`, so no provider API key is required:

```yaml
name: AI Lean Check

on:
  pull_request:
    types: [opened, synchronize, reopened]

permissions:
  contents: read
  models: read

jobs:
  ai-lean-check:
    # GitHub does not pass repository secrets to workflows from forks.
    if: github.event.pull_request.head.repo.full_name == github.repository
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0

      - uses: eyalk11/ai-lean-check@v1
        with:
          provider: github
          source-paths: |
            *.lean
            **/*.lean
          context-files: |
            lakefile.toml
            lean-toolchain
          imports: MyProject
```

The caller must be a Lake project with a committed `lean-toolchain`. By default,
the action runs `leanprover/lean-action@v1` and `lake build` before generating
the additional check.

## Complete configuration reference

All inputs are optional. GitHub Actions passes composite-action inputs as
strings, so boolean values must be written as `true` or `false`.

### Model selection and instructions

| Input | Default | Accepted values and behavior |
| --- | --- | --- |
| `provider` | `github` | `github`, `openai`, `anthropic`, `xai`, `claude-code`, or `codex` |
| `model` | empty | A model ID understood by the selected provider; empty uses the provider default below |
| `task` | Generate meaningful compile-time checks for the changed Lean declarations. | Free-form project instructions appended to the protected system prompt |

Provider defaults are:

| Provider | Default model | Authentication |
| --- | --- | --- |
| `github` | `openai/gpt-4o` | Automatic `GITHUB_TOKEN`; caller needs `models: read` |
| `openai` | `gpt-5.6-terra` | `OPENAI_API_KEY` secret |
| `anthropic` | `claude-sonnet-4-6` | `ANTHROPIC_API_KEY` secret |
| `xai` | `grok-4.5` | `XAI_API_KEY` secret |
| `claude-code` | Claude Code default | `CLAUDE_CODE_OAUTH_TOKEN` or `ANTHROPIC_API_KEY` |
| `codex` | Codex default | `OPENAI_API_KEY` |

The model ID is not rewritten or validated against a static catalog. This lets
callers select newer or account-specific models without updating the action.

### Diff and context selection

| Input | Default | Accepted values and behavior |
| --- | --- | --- |
| `source-paths` | `*.lean` and `**/*.lean` | Newline-separated Git pathspecs. Only matching changes are included in the PR diff and placeholder scan |
| `context-files` | empty | Newline-separated recursive filesystem glob patterns. Matching UTF-8 text files are included as read-only context |
| `imports` | empty | Newline-separated Lean module names. Every generated candidate must contain each exact `import MODULE` line |
| `base-sha` | PR base SHA | Git commit/ref used as the left side of the diff |
| `head-sha` | PR head SHA, otherwise `HEAD` | Git commit/ref used as the right side of the diff |

The action normally computes `base-sha...head-sha`. If the merge-base form
fails, it falls back to a direct two-commit diff. Checkout must therefore use
`fetch-depth: 0`, or the caller must ensure both selected commits are present.

If no matching changes exist, generation is skipped successfully,
`generated-file` is empty, and `attempts` is `0`.

### Token and retry limits

| Input | Default | Accepted values and behavior |
| --- | --- | --- |
| `max-input-tokens` | `50000` | Positive integer approximate cap for the combined diff and context; set to an empty string to use `max-context-bytes` |
| `max-context-bytes` | `200000` | Positive integer legacy byte cap; ignored when `max-input-tokens` is non-empty |
| `max-output-tokens` | `32768` | Positive integer sent to the provider for each model call |
| `max-repair-attempts` | `2` | Non-negative integer repairs after the initial attempt; total calls are at most this value plus one |
| `agent-max-turns` | `20` | Positive integer maximum turns for `claude-code`; unused by direct providers and Codex |

`max-input-tokens` is converted to a byte budget using four UTF-8 bytes per
token. This is deliberately approximate because each provider uses a different
tokenizer. Truncation affects the end of the combined context; the diff is
placed first so it has priority.

The `32768` output-token default leaves room for substantial Lean definitions,
proof terms, and compiler-driven repair. It is a maximum, not a reservation:
providers charge for tokens actually generated. Lower it for short declaration
checks or raise it when the selected model supports a larger output and the
formalization genuinely requires it.

Token caps constrain usage but do not establish a monetary budget. Provider
prices, hidden reasoning tokens, caching, and failed/retried calls can differ.

### Lean setup, build, and artifacts

| Input | Default | Accepted values and behavior |
| --- | --- | --- |
| `setup-lean` | `true` | `true` runs `leanprover/lean-action@v1`; `false` requires `lake` and the correct Lean toolchain to already be available |
| `build-project` | `true` | `true` runs `lake build` in the setup action; only used when `setup-lean: true` |
| `use-mathlib-cache` | `auto` | `auto`, `true`, or `false`, passed unchanged to `leanprover/lean-action` |
| `output-file` | `.ai-lean-check/GeneratedCheck.lean` | Workspace-relative path for the last generated candidate |
| `upload-artifact` | `true` | `true` uploads the candidate and its `.diagnostics.txt` file as the `ai-lean-check` artifact |

The generated candidate is compiled with:

```sh
lake env lean <output-file>
```

Provider credentials are removed from that compiler process. The action never
commits or pushes the generated file.

### Assumption and `sorry` policy

| Input | Default | Accepted values and behavior |
| --- | --- | --- |
| `deps-sorry-policy` | `warn` | `warn` annotates dependency assumptions; `reject` fails if dependency files contain placeholders |
| `sorry-allowed-files` | `**/*_deps.lean` | Newline-separated path globs in which baseline `sorry` or `admit` declarations are allowed |

Any `sorry` or `admit` outside a path matched by `sorry-allowed-files` always
fails. This rule cannot be downgraded to a warning. `deps-sorry-policy` controls
only placeholders found inside designated dependency files. The generated
candidate always rejects placeholders regardless of either setting. Project
scanning is limited to tracked files matched by `source-paths`.

### Direct-provider examples

Anthropic:

```yaml
with:
  provider: anthropic
  model: claude-sonnet-4-6
env:
  ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}
```

Use `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, or `XAI_API_KEY` for the respective
direct provider. GitHub Models instead requires `permissions: models: read`.

OpenAI:

```yaml
- uses: eyalk11/ai-lean-check@v1
  with:
    provider: openai
    model: gpt-5.6-terra
    max-input-tokens: "25000"
    max-output-tokens: "4096"
  env:
    OPENAI_API_KEY: ${{ secrets.OPENAI_API_KEY }}
```

xAI:

```yaml
- uses: eyalk11/ai-lean-check@v1
  with:
    provider: xai
    model: grok-4.5
  env:
    XAI_API_KEY: ${{ secrets.XAI_API_KEY }}
```

### Coding-agent mode

Claude Code with a long-lived OAuth token:

```yaml
permissions:
  contents: read

steps:
  - uses: actions/checkout@v4
    with:
      fetch-depth: 0

  - uses: eyalk11/ai-lean-check@v1
    with:
      provider: claude-code
      agent-max-turns: "20"
      imports: MyProject
    env:
      CLAUDE_CODE_OAUTH_TOKEN: ${{ secrets.CLAUDE_CODE_OAUTH_TOKEN }}
```

Codex:

```yaml
- uses: eyalk11/ai-lean-check@v1
  with:
    provider: codex
    model: gpt-5.6-sol
    imports: MyProject
  env:
    OPENAI_API_KEY: ${{ secrets.OPENAI_API_KEY }}
```

Neither coding agent receives `GITHUB_TOKEN` or `GH_TOKEN`; both variables are
explicitly empty in the agent environment. Repository permissions can remain
`contents: read`. Claude Code runs through Anthropic's base action and its only
shell permission is a generated wrapper that removes provider and GitHub
credentials before running Lean. Codex runs through the official action in
`workspace-write` sandbox mode with `drop-sudo`.

After the agent finishes, a separate verifier confirms that `HEAD` and all
tracked files are unchanged, rejects forbidden constructs, removes provider and
GitHub credentials from the compiler environment, reruns `lake env lean
<generated-file>` and `lake build`, and records the commands, exit codes,
standard output, and standard error in
`<generated-file>.diagnostics.txt`. This log—not the agent's claim—is
authoritative.

### Outputs

| Output | Value |
| --- | --- |
| `generated-file` | Workspace-relative path to the last candidate, or empty when generation was skipped |
| `attempts` | Number of model calls performed, including the initial generation |

Reference outputs by assigning an `id`:

```yaml
- id: lean-ai
  uses: eyalk11/ai-lean-check@v1

- name: Show result
  run: |
    echo "file=${{ steps.lean-ai.outputs.generated-file }}"
    echo "attempts=${{ steps.lean-ai.outputs.attempts }}"
```

### Failure and warning behavior

The action fails when:

- provider configuration or credentials are invalid;
- commits required for diff collection are unavailable;
    - any tracked source file outside `sorry-allowed-files` contains `sorry` or
      `admit`;
    - `deps-sorry-policy: reject` finds a placeholder inside a dependency file;
- model output contains a forbidden construct or misses a required import after
  all attempts;
- the generated candidate still does not compile after all attempts;
- project setup or the optional initial `lake build` fails.

The action succeeds with warnings only when `deps-sorry-policy: warn` finds
placeholders inside designated dependency files. Misplaced placeholders always
fail. The artifact upload uses `if: always()`, so the last candidate and
diagnostics are retained after generation or compilation failures whenever they
exist.

## Full example

```yaml
name: AI Lean Check

on:
  pull_request:
    types: [opened, synchronize, reopened]
  workflow_dispatch:

permissions:
  contents: read
  models: read

jobs:
  ai-lean-check:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0

      - id: ai-check
        uses: eyalk11/ai-lean-check@v1
        with:
          provider: github
          model: openai/gpt-4o
          source-paths: |
            Lean/**/*.lean
          context-files: |
            lakefile.toml
            lean-toolchain
            Lean/**/*_deps.lean
          imports: |
            MyProject
          task: |
            Generate focused examples that exercise changed declarations.
            Treat declarations imported from *_deps.lean as explicit assumptions.
          max-input-tokens: "30000"
          max-output-tokens: "4096"
          max-repair-attempts: "2"
          deps-sorry-policy: warn
          sorry-allowed-files: |
            Lean/**/*_deps.lean
          setup-lean: "true"
          build-project: "true"
          use-mathlib-cache: auto
          output-file: .ai-lean-check/GeneratedCheck.lean
          upload-artifact: "true"
```

## Security model

Model output is untrusted. Before compilation, the action rejects `sorry`,
`admit`, new axioms, unsafe declarations, `run_cmd`, `#eval`, `#compile`,
initializers, foreign functions, and direct `IO`/`System` access. The workflow
uses read-only repository permissions and never commits generated code.

Do not use `pull_request_target` to check out and execute an untrusted fork with
repository secrets. For forked pull requests, keep ordinary deterministic Lean
CI enabled and run this AI check only after a maintainer-controlled approval
flow.

Compilation proves only that the generated check is accepted by Lean. Review
the generated artifact to decide whether the check is mathematically useful.

Baseline assumptions should be isolated in clearly named files:

```text
Lean/theorem_3_11_deps.lean
Lean/theorem_4_2_deps.lean
```

The default pattern `**/*_deps.lean` designates the only files in which
`sorry` or `admit` may appear. A placeholder anywhere else always fails the
job. Within dependency files, `deps-sorry-policy: warn` emits GitHub warning
annotations and continues, while `deps-sorry-policy: reject` fails the job.

`max-input-tokens` is enforced using a conservative four-UTF-8-bytes-per-token
estimate because providers use different tokenizers. `max-output-tokens` is
sent directly to the selected provider on every generation or repair attempt.

## Development

The implementation uses only Python's standard library:

```sh
python -m unittest discover -s tests -v
```

## License

MIT
