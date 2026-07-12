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
