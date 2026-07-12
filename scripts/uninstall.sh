#!/usr/bin/env bash
set -euo pipefail

CODEX_HOME="${CODEX_HOME:-$HOME/.codex}"
BINARY_PATH="$CODEX_HOME/hooks/compaction_guard"
HOOKS_PATH="$CODEX_HOME/hooks.json"
STATE_DIR="$CODEX_HOME/compaction-guard"
BACKUP_DIR="$STATE_DIR/backups"
PURGE_STATE=false

usage() {
  printf '%s\n' \
    "Usage: scripts/uninstall.sh [--purge-state]" \
    "" \
    "Removes this guard's hook groups and installed binary while preserving" \
    "all unrelated hooks. State and backups are retained unless --purge-state" \
    "is supplied."
}

for argument in "$@"; do
  case "$argument" in
    --purge-state) PURGE_STATE=true ;;
    -h|--help) usage; exit 0 ;;
    *) printf 'Unknown argument: %s\n' "$argument" >&2; usage >&2; exit 2 ;;
  esac
done

if [[ -f "$HOOKS_PATH" ]]; then
  if ! command -v jq >/dev/null 2>&1; then
    printf 'Required command not found: jq\n' >&2
    exit 1
  fi
  install -d -m 700 "$BACKUP_DIR"
  timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
  install -m 600 "$HOOKS_PATH" "$BACKUP_DIR/hooks.json.before-uninstall.$timestamp"
  temporary="$(mktemp "${TMPDIR:-/tmp}/codex-compaction-guard-uninstall.XXXXXX")"
  hooks_stage="$CODEX_HOME/.hooks.json.uninstall.$$.tmp"
  trap 'rm -f "$temporary" "${hooks_stage:-}"' EXIT
  jq \
    --arg command "$BINARY_PATH" \
    --arg legacy_abs "/usr/bin/python3 $CODEX_HOME/hooks/compaction_guard.py" \
    --arg legacy "python3 $CODEX_HOME/hooks/compaction_guard.py" '
    def is_owned_command:
      . == $command or . == $legacy_abs or . == $legacy;
    def strip_owned_handlers:
      .hooks = [
        .hooks[]? |
        select((((.command // "") | is_owned_command) | not))
      ] |
      select((.hooks | length) > 0);

    .hooks = ((.hooks // {}) |
      with_entries(.value = [ .value[] | strip_owned_handlers ]) |
      with_entries(select((.value | length) > 0))
    )
  ' "$HOOKS_PATH" >"$temporary"
  jq empty "$temporary"
  install -m 600 "$temporary" "$hooks_stage"
  mv -f "$hooks_stage" "$HOOKS_PATH"
fi

rm -f "$BINARY_PATH"
if [[ "$PURGE_STATE" == true ]]; then
  rm -rf "$STATE_DIR"
fi

printf '%s\n' \
  "Removed binary: $BINARY_PATH" \
  "Removed guard hook groups from: $HOOKS_PATH"
if [[ "$PURGE_STATE" == false ]]; then
  printf 'Retained state and backups: %s\n' "$STATE_DIR"
fi
