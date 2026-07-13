# Architecture

## Lifecycle

```text
PreCompact
  -> classify root versus subagent from agent_id or first session_meta
  -> subagent: no checkpoint and no pending state
  -> resolve canonical transcript ownership (or same-turn fallback scope)
  -> extract deterministic checkpoint
  -> atomic checkpoint.json

PostCompact
  -> inspect latest compacted record
  -> for transcript-backed state, prove generation advanced beyond the PreCompact baseline
  -> classify empty / weak / healthy
  -> atomically create one immutable checkpoint+generation claim
  -> healthy: audit suppression and use only the built-in summary
  -> recovery: atomic pending.json
  -> no-op when this checkpoint/generation is already armed

PreToolUse (same turn)
  -> match session, transcript scope, turn, checkpoint identity, and cwd
  -> atomically rename pending.json to consumed-*.json
  -> return only hookSpecificOutput.additionalContext

PostToolUse (same turn, Bash only)
  -> cover write_stdin completion paths that have no PreToolUse
  -> match session, transcript scope, turn, checkpoint identity, and cwd
  -> atomically rename pending.json to consumed-*.json
  -> return only hookSpecificOutput.additionalContext

Stop
  -> resolve state from transcript_path
  -> reject recursive stop_hook_active
  -> atomically rename pending.json to consumed-*.json
  -> return decision=block with local recovery

SubagentStop
  -> resolve child state only from agent_transcript_path
  -> never fall back to root pending state
  -> never inject model-visible local context
  -> atomically suppress pending state left by older guard versions

SessionStart or UserPromptSubmit
  -> fallback one-shot additionalContext injection only for transcript-backed state
```

## State ownership

Root transcript-backed state lives under
`<session>--transcript-<32-hex SHA-256 prefix>`, where the digest input is the
canonical transcript path. Normal root events use `transcript_path`.
Subagents are detected first from `agent_id`, then from the first
`session_meta.payload.source.subagent` or `thread_source=subagent` marker.
They do not create local checkpoints. `SubagentStop` uses
`agent_transcript_path` only to find and suppress legacy child pending state.

When no transcript path exists, state lives under `<session>--turn-<turn>`.
That scope can deliver only inside the same turn because cross-turn ownership
cannot be proved. Compatible schema-v2 `<session>--root` and
`<session>--<agent>` directories are migrated lazily into transcript scope.

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

## Recovery policy

- `recovery`: built-in summary is missing, short, or contains reset-like text.
- healthy: the built-in summary remains authoritative and no model-visible
  local context is added.
- subagent: all local checkpoint and delivery paths are bypassed. Codex clones
  parent model context into new children, so even correctly scoped child
  enrichment would duplicate parent history and amplify context growth.

The private root checkpoint may retain up to 40k characters. Recovery delivery
is capped at 16k. The renderer keeps the assessment, temporal header,
continuation contract, and closing tag. It also states that a copied recovery
block is inherited parent history, not a spawned agent's active task.

## Same-turn delivery order

Stable Codex accepts only the common output fields from `PostCompact`, so
`PostCompact` can arm root recovery but cannot inject context. Delivery
surfaces then race for the same one-shot pending file:

1. `PreToolUse` delivers at the first hook-eligible direct or nested tool call
   of the same turn. The event must match the pending `session_id`, `turn_id`,
   transcript scope, `checkpoint_id`, and normalized `cwd`; any mismatch fails open and
   preserves the pending state for a later surface.
2. Bash `PostToolUse` covers `write_stdin`: Codex intentionally skips
   `PreToolUse` for that transport call but can emit the original command's
   Bash `PostToolUse` when the process completes.
3. Root `Stop` delivers only for its own transcript. `SubagentStop` never
   delivers and never consumes root state.
4. `SessionStart` (compact/resume) and `UserPromptSubmit` deliver across turns
   and resumed sessions without turn binding only when `cross_turn_safe=true`.

The outer code-mode `functions.exec` call uses a custom payload and does not
emit tool-use lifecycle hooks. Nested calls re-enter normal dispatch, so an
eligible `tools.exec_command` emits `PreToolUse` with canonical tool name
`Bash`; `functions.wait` emits neither tool-use event.

Tool-boundary responses contain only `hookSpecificOutput.additionalContext`.
Stable Codex rejects gating fields on `PreToolUse`, and `PostToolUse` runs after
the side effect, so the guard never emits decisions, permission fields, input
rewrites, or output replacement fields on either delivery surface.

`PreToolUse` runs before supported tool calls permanently; Bash `PostToolUse`
runs after supported shell completions. The steady-state no-pending path still
canonicalizes transcript ownership and probes compatible legacy state, but it
does not parse the current checkpoint until live pending state exists.

## One-shot concurrency

Consumers do not write a consumed copy and then delete pending state. They first
atomically rename `pending.json`. Only the process that wins the rename can
inject. Concurrent losers return `{"continue":true}`. A legacy healthy or
subagent pending file is instead atomically renamed to `suppressed-*.json` and
never becomes model-visible.

`checkpoint_id` binds pending state to the exact checkpoint and prevents stale
pending state from being applied after a newer compaction. `PostCompact`
additionally compares the latest compaction with the checkpoint baseline:
numeric `window_number` is authoritative when available, with parsed RFC3339
timestamp as fallback. Equal, older, or unprovable generations do not arm.
Repeated Post callbacks for the same checkpoint and generation are idempotent.
An immutable `claimed-generation-*.json` file is acquired with `create_new`
before pending state is armed, closing the Post/Post/consumer race. The guard
also checks live `pending.json` and durable `consumed-*.json` records, so a
delayed callback cannot re-arm a generation after delivery.

## Time and size budgets

- Cooperative full hook process budget: 12 seconds.
- Individual git command budget: 2 seconds.
- Command stdout cap: 128 KiB.
- Transcript scan: the most recent 16 MiB, with periodic deadline checks.
- Recent timeline budget: 10k characters.
- Fresh file context budget: 12k characters.
- Private checkpoint context: 40k characters.
- Model-visible recovery context: 16k characters.

The checkpoint renderer reserves space for the temporal header and
continuation footer before truncating the middle. Recovery delivery applies its
own middle truncation and records both `injection_budget_chars` and actual
`injected_chars` after the one-shot consumer wins.

## Compatibility boundary

Codex officially provides hook event JSON but documents that the transcript
format is not stable. The parser therefore:

- ignores unknown fields;
- skips malformed JSONL rows;
- accepts tool input as a string or JSON value;
- treats missing optional fields as absent state;
- fails open on all runtime errors.
