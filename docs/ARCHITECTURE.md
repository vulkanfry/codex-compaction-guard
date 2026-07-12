# Architecture

## Lifecycle

```text
PreCompact
  -> extract deterministic checkpoint
  -> atomic checkpoint.json

PostCompact
  -> inspect latest compacted record
  -> classify empty / weak / healthy
  -> atomic pending.json

PreToolUse (same turn)
  -> match session, turn, checkpoint identity, and cwd
  -> atomically rename pending.json to consumed-*.json
  -> return only hookSpecificOutput.additionalContext

PostToolUse (same turn, Bash only)
  -> cover write_stdin completion paths that have no PreToolUse
  -> match session, turn, checkpoint identity, and cwd
  -> atomically rename pending.json to consumed-*.json
  -> return only hookSpecificOutput.additionalContext

Stop or SubagentStop
  -> reject recursive stop_hook_active
  -> atomically rename pending.json to consumed-*.json
  -> return decision=block with local enrichment

SessionStart or UserPromptSubmit
  -> fallback one-shot additionalContext injection
```

## Checkpoint content

The extractor streams the JSONL transcript and keeps bounded state:

- active goal and latest explicit request;
- previous built-in summary anchor;
- chronological recent messages, tool calls, and tool results;
- recently patched paths;
- git and worktree metadata;
- bounded fresh file diffs or excerpts;
- bounded project evidence tails.

No model is called. Live files are read only from within the resolved repository
root. Sensitive names, binary files, and files above 2 MiB are excluded.

## Recovery modes

- `recovery`: built-in summary is missing, short, or contains reset-like text.
- `enrichment`: built-in summary is healthy and remains authoritative for newer
  or conflicting facts.

Both modes inject local state because deterministic operational detail can be
useful even when the model-generated summary is good.

## Same-turn delivery order

Stable Codex accepts only the common output fields from `PostCompact`, so
`PostCompact` can arm state but cannot inject context. Delivery surfaces then
race for the same one-shot pending file:

1. `PreToolUse` delivers at the first hook-eligible direct or nested tool call
   of the same turn. The event must match the pending `session_id`, `turn_id`,
   `checkpoint_id`, and normalized `cwd`; any mismatch fails open and
   preserves the pending state for a later surface.
2. Bash `PostToolUse` covers `write_stdin`: Codex intentionally skips
   `PreToolUse` for that transport call but can emit the original command's
   Bash `PostToolUse` when the process completes.
3. `Stop` and `SubagentStop` deliver when the turn ends without another tool
   call.
4. `SessionStart` (compact/resume) and `UserPromptSubmit` deliver across turns
   and resumed sessions without turn binding.

The outer code-mode `functions.exec` call uses a custom payload and does not
emit tool-use lifecycle hooks. Nested calls re-enter normal dispatch, so an
eligible `tools.exec_command` emits `PreToolUse` with canonical tool name
`Bash`; `functions.wait` emits neither tool-use event.

Tool-boundary responses contain only `hookSpecificOutput.additionalContext`.
Stable Codex rejects gating fields on `PreToolUse`, and `PostToolUse` runs after
the side effect, so the guard never emits decisions, permission fields, input
rewrites, or output replacement fields on either delivery surface.

`PreToolUse` runs before supported tool calls permanently; Bash `PostToolUse`
runs after supported shell completions. Without pending state each hook performs
one failed `open()` of `pending.json` and exits; the checkpoint file is parsed
only after live pending state exists.

## One-shot concurrency

Consumers do not write a consumed copy and then delete pending state. They first
atomically rename `pending.json`. Only the process that wins the rename can
inject. Concurrent losers return `{"continue":true}`.

`checkpoint_id` binds pending state to the exact checkpoint and prevents stale
pending state from being applied after a newer compaction.

## Time and size budgets

- Cooperative full hook process budget: 12 seconds.
- Individual git command budget: 2 seconds.
- Command stdout cap: 128 KiB.
- Transcript scan: the most recent 16 MiB, with periodic deadline checks.
- Recent timeline budget: 10k characters.
- Fresh file context budget: 12k characters.
- Final restore context: 40k characters.

The final renderer reserves space for the temporal header and continuation
footer before truncating the middle.

## Compatibility boundary

Codex officially provides hook event JSON but documents that the transcript
format is not stable. The parser therefore:

- ignores unknown fields;
- skips malformed JSONL rows;
- accepts tool input as a string or JSON value;
- treats missing optional fields as absent state;
- fails open on all runtime errors.
