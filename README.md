# Codex Compaction Guard

Native Rust lifecycle hook that preserves task state before Codex context
compaction and injects a deterministic local enrichment afterward.

The guard is designed for long-running coding sessions where a compacted
summary may be empty, weak, or missing operational details such as the active
goal, recent file changes, git state, proof logs, and the next unresolved step.

## What it does

1. `PreCompact` writes an atomic private checkpoint.
2. `PostCompact` classifies the built-in summary as `empty`, `weak`, or
   `healthy`, then always arms a one-shot continuation.
3. The first hook-eligible `PreToolUse` of the same turn injects the checkpoint
   as `additionalContext` before that direct or nested tool executes.
4. A Bash-only `PostToolUse` closes Codex's `write_stdin` gap, where the
   resumed terminal call intentionally has no `PreToolUse` event.
5. If the turn runs no further supported tool call, the first `Stop` or `SubagentStop`
   injects the checkpoint and blocks the premature stop once.
6. `SessionStart` and `UserPromptSubmit` are fallback injection paths when the
   original turn was interrupted.

### Delivery timing

Current Codex releases accept only the common output fields from `PostCompact`,
so a hook cannot attach `additionalContext` to the compaction event itself. The
guard therefore arms enrichment at `PostCompact` and delivers it at the first
supported surface:

1. `PreToolUse` in the same turn. Auto-compaction usually interrupts a running
   turn that continues with more tool calls, so the enrichment reaches the
   model at the first hook-eligible direct or nested tool call instead of
   waiting for the turn to end.
2. Bash `PostToolUse` when a `write_stdin` poll observes completion of an
   existing `exec_command`. Stable Codex deliberately skips `PreToolUse` for
   `write_stdin`, so this closes the post-only completion path.
3. `Stop` or `SubagentStop` when the turn ends without another supported tool
   call.
4. `SessionStart` (compact/resume) and `UserPromptSubmit` across turns.

Codex's outer code-mode `functions.exec` custom call is not itself a lifecycle
hook boundary. Eligible nested calls, such as `tools.exec_command`, enter the
normal dispatcher and are reported to hooks under the canonical tool name
`Bash`. `functions.wait` does not emit either tool-use hook.

All delivery surfaces race for the same one-shot pending file, so exactly one
of them injects. Both tool-boundary responses deliberately contain only
`hookSpecificOutput.additionalContext`. Stable Codex rejects gating fields on
`PreToolUse`, and a `PostToolUse` decision cannot undo a completed command, so
the guard never gates, rewrites, or replaces the tool call it rides on.

Every injected snapshot explicitly tells the model that:

- this is an additional local compaction;
- quoted actions are past steps, not a new user request;
- the newer built-in summary wins on conflicting facts;
- completed work must not be repeated;
- continuation starts at the first genuinely unresolved step.

## Safety properties

- Native Rust runtime; no model or network call from the hook.
- Fails open and emits exactly one JSON line on hook errors.
- Private state directories use mode `0700`; state files use `0600`.
- Checkpoints and pending state are written atomically.
- Concurrent delivery hooks, across tool-boundary and stop surfaces, can
  consume a pending enrichment only once.
- The tool-call fast path stays cheap: without pending state the hook performs
  a single failed `open()` and never parses the checkpoint.
- Secret patterns and sensitive files are redacted or excluded before state is
  persisted.
- Git subprocesses have per-command and process-wide deadlines.
- Restore context is bounded to 40,000 Unicode characters while retaining the
  temporal header, continuation contract, and closing tag.

## Requirements

- macOS or Linux
- Rust `1.88+`
- `jq`
- Codex with lifecycle hooks support
- Python 3 only for the integration test suite, not for runtime

## Install

Review the installer first, then run:

```bash
git clone <repository-url> codex-compaction-guard
cd codex-compaction-guard
./scripts/verify.sh
./scripts/install.sh
```

The installer:

- builds with `cargo build --release --locked`;
- installs the binary at `~/.codex/hooks/compaction_guard`;
- backs up the existing `~/.codex/hooks.json`;
- merges eight guard hook groups without replacing unrelated hooks;
- enables the documented `hooks` feature.

Codex intentionally does not trust changed hooks automatically. Open a fresh
Codex CLI session, run `/hooks`, review all eight definitions, and trust them.
Do not start the real-compaction proof until all eight rows show `Active = 1`.

The installer deliberately does not change `remote_compaction_v2` or any other
unrelated Codex feature. Configure those separately in Codex if desired; the
guard does not depend on that feature.

## Verify the installed surface

```bash
CODEX_COMPACTION_GUARD_EXECUTABLE="$HOME/.codex/hooks/compaction_guard" \
  python3 -m unittest -v tests/test_hook_lifecycle.py
```

The lifecycle suite covers 20 scenarios:

- empty, weak, and healthy built-in summaries;
- enrichment versus recovery mode;
- same-turn `PreToolUse` delivery with a strict schema-safe output shape and
  no later duplicate `Stop` injection;
- Bash `PostToolUse` delivery for the `write_stdin` post-only fallback;
- turn binding for the tool-boundary surface;
- agent-scoped state isolation for subagent tool calls;
- chronological recent context;
- staged, unstaged, and untracked files;
- secret redaction and sensitive-file exclusion;
- fallback injection, including `UserPromptSubmit` after a manual compaction;
- recursive `stop_hook_active` handling;
- eight concurrent `Stop` processes with exactly one injection;
- eight concurrent mixed `PreToolUse`/`PostToolUse` processes with exactly one
  injection;
- footer preservation at the 40k context budget.
- private `0700` state directories and `0600` checkpoint files.
- `CODEX_HOME`-scoped state placement.
- root-pending fallback for a documented `SubagentStop` payload.

For the strongest proof, trigger one real compaction in a fresh Codex task and
inspect:

```text
~/.codex/compaction-guard/<session-id>--root/checkpoint.json
~/.codex/compaction-guard/<session-id>--root/audit.jsonl
```

A Rust/schema-v2 checkpoint contains `schema_version: 2` and `checkpoint_id`.
A completed injection adds one `consumed-*.json` record.

## Give the installation to an LLM

Use [docs/LLM_INSTALL.md](docs/LLM_INSTALL.md), or paste this short request:

```text
Install this repository as a user-level Codex compaction guard. Read
docs/LLM_INSTALL.md first. Preserve unrelated hooks, do not bypass or fabricate
hook trust, run scripts/verify.sh, install the release binary, ask me to review
the eight hooks through /hooks, then prove one real PreCompact -> PostCompact
-> delivery flow (PreToolUse, Bash PostToolUse, or Stop/fallback). Report
registered, trusted, real-action-worked, and not-verified separately.
```

Machine-oriented project context is also available in [llms.txt](llms.txt).
Краткая русская инструкция для агента: [docs/LLM_INSTALL_RU.md](docs/LLM_INSTALL_RU.md).

## Uninstall

```bash
./scripts/uninstall.sh
```

This removes only the guard binary and its hook groups. Checkpoints and backups
are retained by default. To remove those as well:

```bash
./scripts/uninstall.sh --purge-state
```

## State captured

- active persisted goal, when available;
- latest explicit user request;
- prior built-in compaction summary;
- bounded chronological user/assistant/tool tail;
- git root, branch, HEAD, status, diff stat, and changed paths;
- bounded diffs or fresh excerpts for up to five recent files;
- tails of `.codex.log`, `.codex/proof-ledger.jsonl`, `.codex/goal.md`, and
  `.codex/continuation.md` when present.

The Codex transcript format is not a stable hook interface. Parsing is
deliberately permissive and malformed rows are skipped.

## Development

```bash
cargo fmt -- --check
cargo clippy --all-targets -- -D warnings
cargo test
cargo build
python3 -m unittest -v tests/test_hook_lifecycle.py
```

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) and
[CONTRIBUTING.md](CONTRIBUTING.md).

## Official Codex references

- [Hooks](https://learn.chatgpt.com/docs/hooks)
- [Advanced configuration: hooks](https://learn.chatgpt.com/docs/config-file/config-advanced#hooks)
- [Configuration reference](https://learn.chatgpt.com/docs/config-file/config-reference#configtoml)

## License

[MIT](LICENSE)
