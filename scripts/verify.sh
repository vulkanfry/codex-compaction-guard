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

bash "$ROOT/tests/test_hook_ownership.sh"

printf '%s\n' 'All build, lifecycle, install, and uninstall checks passed.'
