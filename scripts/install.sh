#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CODEX_HOME="${CODEX_HOME:-$HOME/.codex}"
BINARY_DIR="$CODEX_HOME/hooks"
BINARY_PATH="$BINARY_DIR/compaction_guard"
HOOKS_PATH="$CODEX_HOME/hooks.json"
STATE_DIR="$CODEX_HOME/compaction-guard"
BACKUP_DIR="$STATE_DIR/backups"

usage() {
  printf '%s\n' \
    "Usage: scripts/install.sh" \
    "" \
    "Builds the release binary, installs it under CODEX_HOME, and merges" \
    "the six guard lifecycle hooks into CODEX_HOME/hooks.json." \
    "" \
    "Environment:" \
    "  CODEX_HOME   Codex state directory (default: ~/.codex)"
}

for argument in "$@"; do
  case "$argument" in
    -h|--help) usage; exit 0 ;;
    *) printf 'Unknown argument: %s\n' "$argument" >&2; usage >&2; exit 2 ;;
  esac
done

for dependency in cargo jq install; do
  if ! command -v "$dependency" >/dev/null 2>&1; then
    printf 'Required command not found: %s\n' "$dependency" >&2
    exit 1
  fi
done

cargo build --release --locked --manifest-path "$ROOT/Cargo.toml"

install -d -m 700 "$BINARY_DIR" "$STATE_DIR" "$BACKUP_DIR"
temporary_directory="$(mktemp -d "${TMPDIR:-/tmp}/codex-compaction-guard.XXXXXX")"
binary_stage="$BINARY_DIR/.compaction_guard.$$.tmp"
hooks_stage="$CODEX_HOME/.hooks.json.$$.tmp"
trap 'rm -rf "$temporary_directory"; rm -f "${binary_stage:-}" "${hooks_stage:-}"' EXIT
existing="$temporary_directory/existing.json"
rendered="$temporary_directory/rendered.json"
merged="$temporary_directory/merged.json"

if [[ -f "$HOOKS_PATH" ]]; then
  install -m 600 "$HOOKS_PATH" "$existing"
else
  printf '%s\n' '{"hooks":{}}' >"$existing"
fi

jq --arg command "$BINARY_PATH" '
  walk(
    if type == "object" and .command? == "__CODEX_COMPACTION_GUARD_BINARY__"
    then .command = $command
    else .
    end
  )
' "$ROOT/config/hooks.template.json" >"$rendered"

jq \
  --arg command "$BINARY_PATH" \
  --arg legacy_abs "/usr/bin/python3 $CODEX_HOME/hooks/compaction_guard.py" \
  --arg legacy "python3 $CODEX_HOME/hooks/compaction_guard.py" \
  --slurpfile guard "$rendered" '
  def is_owned_command:
    . == $command or . == $legacy_abs or . == $legacy;
  def strip_owned_handlers:
    .hooks = [
      .hooks[]? |
      select((((.command // "") | is_owned_command) | not))
    ] |
    select((.hooks | length) > 0);

  .hooks = (.hooks // {}) |
  reduce (($guard[0].hooks // {}) | keys[]) as $event
    (.;
      .hooks[$event] =
        ([ (.hooks[$event] // [])[] | strip_owned_handlers ]
        + ($guard[0].hooks[$event] // []))
    )
' "$existing" >"$merged"
jq empty "$merged"

timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
old_binary_backup="$BACKUP_DIR/compaction_guard.$timestamp"
had_binary=false
if [[ -f "$BINARY_PATH" ]]; then
  had_binary=true
  install -m 755 "$BINARY_PATH" "$old_binary_backup"
fi
if [[ -f "$HOOKS_PATH" ]]; then
  install -m 600 "$HOOKS_PATH" "$BACKUP_DIR/hooks.json.$timestamp"
fi

install -m 755 "$ROOT/target/release/codex-compaction-guard" "$binary_stage"
install -m 600 "$merged" "$hooks_stage"
mv -f "$binary_stage" "$BINARY_PATH"
if ! mv -f "$hooks_stage" "$HOOKS_PATH"; then
  if [[ "$had_binary" == true ]]; then
    install -m 755 "$old_binary_backup" "$BINARY_PATH"
  else
    rm -f "$BINARY_PATH"
  fi
  printf 'Failed to install hooks.json; restored the previous binary.\n' >&2
  exit 1
fi

if command -v codex >/dev/null 2>&1; then
  if ! CODEX_HOME="$CODEX_HOME" codex features enable hooks >/dev/null; then
    printf 'Warning: could not enable the hooks feature automatically.\n' >&2
  fi
fi

"$BINARY_PATH" --version
printf '%s\n' \
  "Installed binary: $BINARY_PATH" \
  "Merged hooks:     $HOOKS_PATH" \
  "Private state:    $STATE_DIR" \
  "" \
  "Security step: open /hooks in a fresh Codex CLI session, review all six" \
  "definitions, and trust them. Codex intentionally skips changed hooks until" \
  "their current definitions are reviewed."
