# Repository instructions

This repository builds a security-sensitive Codex lifecycle hook.

## Required checks

Before claiming completion, run:

```bash
./scripts/verify.sh
```

For focused Rust changes, also run:

```bash
cargo fmt -- --check
cargo clippy --all-targets -- -D warnings
cargo test
```

## Invariants

- Hook mode with no arguments reads one JSON object from stdin and emits exactly
  one compact JSON object on stdout.
- Hook errors fail open, exit zero, and do not print diagnostics to stderr.
- `PreToolUse`, `Stop`, and `SubagentStop` enrichment is atomic and one-shot.
- `PreToolUse` delivery is bound to the pending turn and emits only
  `hookSpecificOutput.additionalContext`; it must never emit `continue:false`,
  `decision`, `permissionDecision`, `updatedInput`, `stopReason`, or
  `suppressOutput`.
- `stop_hook_active=true` never consumes pending state.
- User-level installation must preserve unrelated hooks.
- Hook trust requires explicit user review; never synthesize trust hashes.
- Any persisted model-visible or transcript-derived string must be redacted.
- Sensitive files, binary files, and files outside the repository root must not
  enter fresh file context.
- The 40k restore budget must retain the temporal header, continuation contract,
  and closing XML tag.
- Installation must not manage `remote_compaction_v2` or unrelated Codex
  feature flags.

## Editing

- Keep the runtime dependency set small.
- Prefer deterministic local extraction over calling a model or network service.
- Add a lifecycle regression test for behavior changes.
- Update README.md, docs/LLM_INSTALL.md, and CHANGELOG.md when installation or
  hook behavior changes.
