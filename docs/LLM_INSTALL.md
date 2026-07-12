# LLM installation runbook

This document is written for a coding agent installing the repository on a
user's machine.

## Objective

Install the native Rust guard as a user-level Codex hook without overwriting
unrelated hooks, obtain explicit user trust for the exact hook definitions, and
prove the installed lifecycle with a real action.

## Rules

1. Read `README.md`, `SECURITY.md`, and `scripts/install.sh` before changing the
   machine.
2. Do not replace the complete `hooks.json` file. Use the installer merge.
3. Do not fabricate or copy stale `trusted_hash` values.
4. Do not make `--dangerously-bypass-hook-trust` part of persistent setup.
5. Do not print checkpoint bodies or project secrets in the final response.
6. Do not claim a compaction used Rust based only on the configured command.
   Match the compaction timestamp to schema-v2 checkpoint and audit evidence.
7. Existing tasks may cache hook discovery. Use a fresh task or restart Codex
   for the final real-action proof.

## Procedure

### 1. Preflight

```bash
codex --version
rustc --version
cargo --version
jq --version
git status --short
```

Inspect active locations:

```bash
printf 'CODEX_HOME=%s\n' "${CODEX_HOME:-$HOME/.codex}"
test -f "${CODEX_HOME:-$HOME/.codex}/hooks.json" && \
  jq '.hooks | keys' "${CODEX_HOME:-$HOME/.codex}/hooks.json"
```

### 2. Verify the repository

```bash
./scripts/verify.sh
```

Do not install after a failing check.

### 3. Install

Standard installation:

```bash
./scripts/install.sh
```

If the user explicitly requires the current Codex remote compaction v2 feature:

```bash
./scripts/install.sh --enable-remote-compaction-v2
```

The installer checks whether the feature exists before enabling it. The guard
itself does not depend on that private/runtime-discovered feature name.

### 4. Review and trust

Start a fresh Codex CLI session and run:

```text
/hooks
```

Ask the user to review and trust these six exact events:

- `PreCompact`
- `PostCompact`
- `Stop`
- `SubagentStop`
- `SessionStart`
- `UserPromptSubmit`

Expected command:

```text
$CODEX_HOME/hooks/compaction_guard
```

Codex records trust against the current normalized hook hash. Any future hook
definition change correctly returns the hook to review state.

### 5. Registration check

Use `/hooks` or the app-server `hooks/list` method. Confirm for all six hooks:

- enabled: true
- trust status: trusted
- command points to the native binary
- no discovery warnings or errors

### 6. Installed binary test

```bash
CODEX_COMPACTION_GUARD_EXECUTABLE="${CODEX_HOME:-$HOME/.codex}/hooks/compaction_guard" \
  python3 -m unittest -v tests/test_hook_lifecycle.py
```

### 7. Real compaction proof

Use a fresh non-critical Codex task. Add enough context or explicitly request a
manual compaction. After it completes, inspect only metadata:

```bash
state="${CODEX_HOME:-$HOME/.codex}/compaction-guard/<session-id>--root"
jq '{schema_version, checkpoint_id, created_at, turn_id}' "$state/checkpoint.json"
tail -n 10 "$state/audit.jsonl"
find "$state" -maxdepth 1 -name 'consumed-*.json' -print
```

Required evidence:

- checkpoint timestamp matches the new compaction;
- `schema_version` is `2`;
- `checkpoint_id` is non-empty;
- audit contains `checkpoint_saved` and `restore_armed`;
- exactly one path consumes the pending snapshot;
- `Stop` or fallback injection contains the local-compaction and past-steps
  semantics;
- the model continues from an unresolved step instead of asking what to do.

## Failure handling

- `modified` or `untrusted`: use `/hooks`; do not edit trust hashes manually.
- No fresh checkpoint: restart Codex or use a fresh task, then retry.
- Fresh checkpoint but no consume: inspect whether the task stopped, resumed, or
  received a user prompt; the fallback may still be armed.
- Empty built-in summary: expected mode is `recovery`.
- Healthy built-in summary: expected mode is `enrichment`.
- Hook timeout: inspect the private audit file; the hook must still fail open.

## Required final report

Report these separately:

- Registered: binary path and six discovered hooks.
- Trusted: whether the exact current definitions are trusted.
- Real action worked: evidence from one matching compaction lifecycle.
- Not verified: anything not proven on the installed surface.

Do not collapse “configured” and “worked” into one status.
