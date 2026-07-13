# Codex Compaction Guard

Native Rust lifecycle hook that preserves root-task state before Codex context
compaction and injects deterministic local recovery only when the built-in
compaction is empty, weak, or unavailable.

The guard is designed for long-running coding sessions where a compacted
summary may be empty, weak, or missing operational details such as the active
goal, recent file changes, git state, proof logs, and the next unresolved step.

## What it does

1. Root `PreCompact` writes an atomic private checkpoint. Subagent compactions
   are deliberately bypassed because Codex clones parent model context into
   every spawned child.
2. `PostCompact` validates the matching checkpoint and, when a transcript is
   visible, proves that a newer compaction generation exists. It classifies the
   built-in summary as `empty`, `weak`, or `healthy`. Healthy compactions use
   only the built-in summary; recovery cases arm a one-shot continuation.
3. For an armed root recovery, the first hook-eligible `PreToolUse` of the same
   turn injects the checkpoint as `additionalContext` before that direct or
   nested tool executes.
4. A Bash-only `PostToolUse` closes Codex's `write_stdin` gap, where the
   resumed terminal call intentionally has no `PreToolUse` event.
5. If the root turn runs no further supported tool call, `Stop` consumes the
   recovery once and blocks the premature stop. `SubagentStop` never injects;
   it only suppresses pending state left by an older guard version.
6. `SessionStart` and `UserPromptSubmit` are fallback injection paths when the
   original turn was interrupted.

### Delivery timing

Current Codex releases accept only the common output fields from `PostCompact`,
so a hook cannot attach `additionalContext` to the compaction event itself. The
guard therefore arms recovery at `PostCompact` and delivers it at the first
supported root surface:

1. `PreToolUse` in the same turn. Auto-compaction usually interrupts a running
   turn that continues with more tool calls, so the recovery reaches the
   model at the first hook-eligible direct or nested tool call instead of
   waiting for the turn to end.
2. Bash `PostToolUse` when a `write_stdin` poll observes completion of an
   existing `exec_command`. Stable Codex deliberately skips `PreToolUse` for
   `write_stdin`, so this closes the post-only completion path.
3. Root `Stop` when the turn ends without another supported tool call.
4. `SessionStart` (compact/resume) and `UserPromptSubmit` across turns for
   transcript-backed state.

Codex's outer code-mode `functions.exec` custom call is not itself a lifecycle
hook boundary. Eligible nested calls, such as `tools.exec_command`, enter the
normal dispatcher and are reported to hooks under the canonical tool name
`Bash`. `functions.wait` does not emit either tool-use hook.

All root delivery surfaces within a transcript or null-transcript turn scope
race for the same one-shot pending file, so exactly one of them injects. Both
tool-boundary responses deliberately contain only
`hookSpecificOutput.additionalContext`. Stable Codex rejects gating fields on
`PreToolUse`, and a `PostToolUse` decision cannot undo a completed command, so
the guard never gates, rewrites, or replaces the tool call it rides on.

Every injected recovery snapshot explicitly tells the model that:

- this is a recovery-only local compaction;
- if inherited by a spawned subagent, it is parent history rather than that
  agent's active task;
- quoted actions are past steps, not a new user request;
- the newer built-in summary wins on conflicting facts;
- completed work must not be repeated;
- continuation starts at the first genuinely unresolved step.

## Safety properties

- Native Rust runtime; no model or network call from the hook.
- Fails open and emits exactly one JSON line on hook errors.
- Private state directories use mode `0700`; state files use `0600`.
- Checkpoints and pending state are written atomically.
- Each checkpoint/generation is armed by one immutable atomic claim, preventing
  concurrent or delayed `PostCompact` callbacks from re-arming it.
- Concurrent delivery hooks, across tool-boundary and stop surfaces, can
  consume a pending recovery only once.
- The steady-state no-pending path avoids parsing the current checkpoint. It
  still resolves canonical transcript ownership and probes compatible legacy
  state before looking up `pending.json`.
- Secret patterns and sensitive files are redacted or excluded before state is
  persisted.
- Git subprocesses have per-command and process-wide deadlines.
- Healthy compactions never add model-visible local context. Subagents never
  create or consume local compaction state; legacy pending deliveries are
  atomically suppressed during a hot upgrade.
- The private root checkpoint is bounded to 40,000 Unicode characters.
  Model-visible recovery is capped at 16,000 while retaining the assessment,
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
- attempts to enable the documented `hooks` feature and warns if Codex rejects
  that separate feature command.

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

The lifecycle suite covers:

- empty, weak, and healthy built-in summaries;
- healthy-summary no-op versus root recovery delivery;
- same-turn `PreToolUse` delivery with a strict schema-safe output shape and
  no later duplicate `Stop` injection;
- Bash `PostToolUse` delivery for the `write_stdin` post-only fallback;
- turn binding for the tool-boundary surface;
- transcript-scoped ownership and subagent bypass, including classification
  from the first `session_meta` row when `agent_id` is absent;
- stale `PostCompact` rejection by compaction generation, including no re-arm
  during concurrent Post/delivery races or after the same generation has
  already been consumed;
- same-turn-only delivery when no transcript path is available;
- chronological recent context;
- staged, unstaged, and untracked files;
- secret redaction and sensitive-file exclusion;
- fallback injection, including `UserPromptSubmit` after a manual compaction;
- recursive `stop_hook_active` handling;
- eight concurrent `Stop` processes with exactly one injection;
- eight concurrent mixed `PreToolUse`/`PostToolUse` processes with exactly one
  injection;
- footer preservation at the 40k private checkpoint budget;
- oversized healthy no-op and 16k recovery cap, including preserved assessment
  and temporal/continuation framing plus exact audit accounting;
- hot-upgrade suppression of legacy healthy or subagent pending state,
  including concurrent tool/stop races;
- private `0700` state directories and `0600` checkpoint files;
- `CODEX_HOME`-scoped state placement;
- strict `SubagentStop` no-op behavior with no child-to-root pending fallback.

For the strongest proof, trigger one real compaction in a fresh Codex task and
inspect:

```text
~/.codex/compaction-guard/<session-id>--transcript-<32-hex>/checkpoint.json
~/.codex/compaction-guard/<session-id>--transcript-<32-hex>/audit.jsonl
```

The suffix is the first 32 hexadecimal characters of SHA-256 over the root
canonical transcript path. Events without a transcript path use
`<session-id>--turn-<turn-id>` and are intentionally ineligible for cross-turn
fallback delivery. Subagent detection uses `agent_id` with the first
`session_meta` row as fallback; child compactions do not create new guard
state. Legacy schema-v2 root/agent directories are migrated only when needed
to suppress or safely consume existing state.

A Rust/schema-v3 checkpoint contains `schema_version: 3`, `checkpoint_id`,
`scope_key`, and `scope_path`. A completed injection adds one
`consumed-*.json` record with `injected_chars` and
`injection_budget_chars`; the matching `restore_consumed` audit row records
the same delivery accounting. Healthy compaction ends at a
`restore_suppressed` audit row without pending state. A hot-upgrade discard is
also retained as `suppressed-*.json`.

## Give the installation to an LLM

Use [docs/LLM_INSTALL.md](docs/LLM_INSTALL.md), or paste this short request:

```text
Install this repository as a user-level Codex compaction guard. Read
docs/LLM_INSTALL.md first. Preserve unrelated hooks, do not bypass or fabricate
hook trust, run scripts/verify.sh, install the release binary, ask me to review
the eight hooks through /hooks, then prove that a healthy root compaction adds
no local model context, a recovery compaction delivers once, and a subagent
compaction is bypassed. Report registered, trusted, real-action-worked, and
not-verified separately.
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

Only root tasks are captured. Subagent transcripts are deliberately bypassed.

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
