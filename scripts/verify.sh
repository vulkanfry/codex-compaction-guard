#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

cargo fmt --manifest-path "$ROOT/Cargo.toml" -- --check
cargo clippy --manifest-path "$ROOT/Cargo.toml" --all-targets -- -D warnings
cargo test --manifest-path "$ROOT/Cargo.toml"
cargo build --locked --manifest-path "$ROOT/Cargo.toml"
cargo build --release --locked --manifest-path "$ROOT/Cargo.toml"
jq empty "$ROOT/config/hooks.template.json"

CODEX_COMPACTION_GUARD_EXECUTABLE="$ROOT/target/debug/codex-compaction-guard" \
  python3 -m unittest -v "$ROOT/tests/test_hook_lifecycle.py"
CODEX_COMPACTION_GUARD_EXECUTABLE="$ROOT/target/release/codex-compaction-guard" \
  python3 -m unittest -v "$ROOT/tests/test_hook_lifecycle.py"

temporary_codex_home="$(mktemp -d "${TMPDIR:-/tmp}/codex-compaction-guard-home.XXXXXX")"
trap 'rm -rf "$temporary_codex_home"' EXIT

install -d -m 700 "$temporary_codex_home"
jq -n --arg home "$temporary_codex_home" '{
  hooks: {
    Stop: [
      {
        hooks: [
          {type: "command", command: "/usr/bin/true", timeout: 5},
          {type: "command", command: ($home + "/hooks/compaction_guard"), timeout: 5},
          {type: "command", command: "/opt/other/compaction_guard", timeout: 5}
        ]
      }
    ]
  }
}' >"$temporary_codex_home/hooks.json"
chmod 600 "$temporary_codex_home/hooks.json"

CODEX_HOME="$temporary_codex_home" "$ROOT/scripts/install.sh"
CODEX_COMPACTION_GUARD_EXECUTABLE="$temporary_codex_home/hooks/compaction_guard" \
  python3 -m unittest -v "$ROOT/tests/test_hook_lifecycle.py"

jq -e --arg command "$temporary_codex_home/hooks/compaction_guard" '
  . as $root |
  ([
    "PreCompact",
    "PostCompact",
    "Stop",
    "SubagentStop",
    "SessionStart",
    "UserPromptSubmit"
  ] | map(
    . as $event |
    ([
        $root.hooks[$event][]?.hooks[]? |
        select((.command // "") == $command)
      ] | length == 1)
  ) | all) and
  ([.hooks.Stop[]?.hooks[]? | select(.command == "/usr/bin/true")] | length == 1) and
  ([.hooks.Stop[]?.hooks[]? | select(.command == "/opt/other/compaction_guard")] | length == 1)
' "$temporary_codex_home/hooks.json" >/dev/null

CODEX_HOME="$temporary_codex_home" "$ROOT/scripts/uninstall.sh" --purge-state
if [[ -e "$temporary_codex_home/hooks/compaction_guard" ]]; then
  printf 'uninstall smoke failed: binary still exists\n' >&2
  exit 1
fi
jq -e --arg command "$temporary_codex_home/hooks/compaction_guard" '
  ([.hooks.Stop[]?.hooks[]? | select(.command == "/usr/bin/true")] | length == 1) and
  ([.hooks.Stop[]?.hooks[]? | select(.command == "/opt/other/compaction_guard")] | length == 1) and
  ([.. | objects | .command? | select(. == $command)] | length == 0)
' "$temporary_codex_home/hooks.json" >/dev/null

printf '%s\n' 'All build, lifecycle, install, and uninstall checks passed.'
