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
   Match the compaction timestamp to schema-v3 checkpoint and audit evidence.
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

Do not modify `remote_compaction_v2` or unrelated Codex feature flags as part
of guard installation. They are separate user configuration, and the guard
does not depend on them.

### 4. Review and trust

Start a fresh Codex CLI session and run:

```text
/hooks
```

Ask the user to review and trust these eight exact events:

- `PreCompact`
- `PostCompact`
- `PreToolUse`
- `PostToolUse`
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

Use `/hooks` or the app-server `hooks/list` method. Confirm for all eight
hooks:

- enabled: true
- trust status: trusted
- command points to the native binary
- no discovery warnings or errors

In the CLI table, every installed guard event must show `Active = 1`. Treat
any nonzero `Review` count as a failed prerequisite; newly installed tool hooks
will otherwise be skipped even while older hooks continue to run.

### 6. Installed binary test

```bash
CODEX_COMPACTION_GUARD_EXECUTABLE="${CODEX_HOME:-$HOME/.codex}/hooks/compaction_guard" \
  python3 -m unittest -v tests/test_hook_lifecycle.py
```

### 7. Real compaction proof

Use a fresh non-critical root Codex task. Add enough context or explicitly
request a manual compaction. After it completes, inspect only metadata. One
live compaction proves only the branch it actually triggers; use the installed
lifecycle suite as evidence for the mutually exclusive healthy, recovery, and
subagent branches. Run three live scenarios only when full live-matrix proof is
required.

```bash
state_root="${CODEX_HOME:-$HOME/.codex}/compaction-guard"
find "$state_root" -maxdepth 1 -type d -name '<session-id>--transcript-*' -print
state='<matching-directory-selected-by-checkpoint.scope_path>'
jq '{schema_version, checkpoint_id, created_at, turn_id, scope_key, scope_path, cross_turn_safe, agent_id}' "$state/checkpoint.json"
tail -n 10 "$state/audit.jsonl"
find "$state" -maxdepth 1 -name 'consumed-*.json' -print
```

Required evidence:

- checkpoint timestamp matches the new compaction;
- `schema_version` is `3`;
- `checkpoint_id` is non-empty;
- `scope_path` matches the root transcript that compacted;
- audit contains `checkpoint_saved` and then exactly one policy outcome:
  `restore_suppressed` for a healthy summary or `restore_armed` for recovery;
- healthy root compaction creates no `pending.json`, no `consumed-*.json`, and
  no model-visible local context;
- subagent compaction creates no new checkpoint or pending state, even when
  `agent_id` is absent but first-row `session_meta` identifies a subagent;
- for recovery, exactly one path consumes the pending snapshot: same-turn
  `PreToolUse`, Bash `PostToolUse`, root `Stop`, or a root fallback surface;
- the recovery consumed record and `restore_consumed` audit row agree on
  `consumed_via`, `injected_chars`, and `injection_budget_chars`;
- recovery delivers no more than 16,000 characters even when the private root
  checkpoint is 40,000;
- `SubagentStop` never injects and never falls back to root pending state;
- a recovery injection contains the local-compaction, inherited-parent, and
  past-steps semantics, then continues from an unresolved root step.

## Failure handling

- `modified` or `untrusted`: use `/hooks`; do not edit trust hashes manually.
- No fresh checkpoint: restart Codex or use a fresh task, then retry.
- Fresh checkpoint plus `restore_suppressed`: healthy behavior is complete;
  there should be no consume or model-visible local context.
- Fresh checkpoint plus `restore_armed` but no consume: inspect whether the
  root task stopped, resumed, or received a user prompt; recovery may still be
  armed.
- A `<session>--turn-*` directory has no proven transcript ownership and can
  deliver only inside the same turn; `SessionStart` and `UserPromptSubmit`
  intentionally leave it pending.
- Outer code-mode `functions.exec` rows are not lifecycle boundaries; inspect
  eligible nested tools such as canonical `Bash` before diagnosing a miss.
- Empty, weak, or unavailable built-in summary: expected mode is `recovery`.
- Healthy built-in summary: expected outcome is `restore_suppressed`, with no
  pending or injection.
- A 40k checkpoint is not a 40k model injection: recovery is capped at 16k.
- Hook timeout: inspect the private audit file; the hook must still fail open.

## Required final report

Report these separately:

- Registered: binary path and eight discovered hooks.
- Trusted: whether the exact current definitions are trusted.
- Real action worked: evidence from one matching compaction lifecycle.
- Not verified: anything not proven on the installed surface.

Do not collapse “configured” and “worked” into one status.
