# Changelog

## 0.2.0 - 2026-07-12

- Reimplemented the compaction guard as a native Rust binary.
- Always enrich healthy compactions while recovering empty or weak summaries.
- Added chronological recent context, previous-summary anchoring, and fresh
  bounded file diffs.
- Added atomic one-shot pending consumption and checkpoint identity binding.
- Added secret redaction, sensitive-file exclusion, Unicode-safe budgets, and
  command deadlines.
- Added installer, uninstaller, CI, LLM runbook, and 14 lifecycle tests.
