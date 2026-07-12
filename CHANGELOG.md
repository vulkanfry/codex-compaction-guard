# Changelog

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
