# Changelog

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
