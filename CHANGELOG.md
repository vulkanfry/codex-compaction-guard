# Changelog

## Unreleased

## 0.3.2 - 2026-07-13

- Keep the private local checkpoint budget at 40,000 Unicode characters, but
  cap model-visible delivery separately: 8,000 characters for healthy-summary
  enrichment and 16,000 for empty, weak, or unavailable-summary recovery.
- Preserve the compaction assessment, temporal semantics, continuation
  contract, and closing tag when either delivery budget truncates an oversized
  checkpoint; malformed pending health metadata is normalized before it can
  affect assessment size or audit mode.
- Record the selected `injection_budget_chars` and actual `injected_chars` in
  both the durable consumed record and `restore_consumed` audit event.
- Add oversized healthy and recovery regressions that prevent a full 40k local
  checkpoint from being injected on top of Codex's built-in compacted summary.

## 0.3.1 - 2026-07-13

- Clarify that same-turn delivery occurs on the first hook-eligible direct or
  nested tool call: outer code-mode `functions.exec` and `functions.wait` do
  not emit tool-use lifecycle events, while nested `exec_command` is reported
  as canonical `Bash`.
- Require all eight `/hooks` rows to show `Active = 1` before a live compaction
  proof, and align the lifecycle fixture with Codex's canonical Bash name.
- Scope root and subagent state by the SHA-256 fingerprint of the canonical
  transcript path, using `agent_transcript_path` for `SubagentStop` and keeping
  `agent_id` as optional metadata only.
- Remove child-to-root pending fallback, isolate null-transcript state to the
  same turn, and lazily migrate compatible schema-v2 root/agent directories.
- Reject stale `PostCompact` callbacks unless the transcript shows a newer
  compaction generation, preferring `window_number` and falling back to RFC3339
  timestamps; an immutable atomic per-generation claim prevents concurrent or
  delayed callbacks from rewriting pending state or re-arming after consume.
- Add concurrent root/child and two-child isolation regressions without
  `agent_id`, child tool/stop delivery races, canonical symlink alias coverage,
  and delayed same-turn Post regressions.

## 0.3.0 - 2026-07-12

- Deliver armed enrichment in the same turn through a one-shot `PreToolUse`
  tool-boundary hook bound to session, turn, checkpoint identity, and cwd,
  plus a Bash-only `PostToolUse` fallback for `write_stdin` completion paths;
  `Stop`, `SubagentStop`, `SessionStart`, and `UserPromptSubmit` remain later
  fallbacks.
- Emit only `hookSpecificOutput.additionalContext` from tool-boundary hooks;
  the guard never blocks, rewrites, or replaces the tool call it rides on.
- Skip checkpoint parsing on the per-tool-call fast path until live pending
  state exists.
- Preserve unrelated `PreToolUse` and `PostToolUse` handlers across install,
  reinstall, and uninstall, and extend the ownership regression to the
  eight-event owned surface.
- Extend the lifecycle suite to 20 scenarios: early same-turn injection with a
  strict schema-safe output shape, no duplicate `Stop` injection afterwards,
  Bash `PostToolUse` delivery, turn binding, agent-scoped subagent isolation,
  an eight-way mixed tool-boundary race, and `UserPromptSubmit` delivery after
  manual compaction.

## 0.2.2 - 2026-07-12

- Remove `remote_compaction_v2` management from the installer; global Codex
  feature flags remain separate user configuration.
- Verify that install and reinstall enable only the required `hooks` feature.

## 0.2.1 - 2026-07-12

- Ignore empty state-directory environment overrides and fall back safely.
- Prove the complete custom `CODEX_HOME` compaction lifecycle.
- Accept real child `turn_id` values when `SubagentStop` safely consumes a
  root compaction, bound to the parent transcript and checkpoint identity.
- Add exact hook-ownership regression coverage for install, reinstall, and
  uninstall while preserving unrelated handlers and matcher groups.

## 0.2.0 - 2026-07-12

- Reimplemented the compaction guard as a native Rust binary.
- Always enrich healthy compactions while recovering empty or weak summaries.
- Added chronological recent context, previous-summary anchoring, and fresh
  bounded file diffs.
- Added atomic one-shot pending consumption and checkpoint identity binding.
- Added secret redaction, sensitive-file exclusion, Unicode-safe budgets, and
  command deadlines.
- Added installer, uninstaller, CI, LLM runbook, and 14 lifecycle tests.
