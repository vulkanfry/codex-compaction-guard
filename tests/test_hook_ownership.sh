#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
temporary_codex_home="$(mktemp -d "${TMPDIR:-/tmp}/codex-compaction-guard-ownership.XXXXXX")"
trap 'rm -rf "$temporary_codex_home"' EXIT

project_command="$temporary_codex_home/hooks/compaction_guard"
other_command="/opt/other/compaction_guard"
expected_after_uninstall="$temporary_codex_home/expected-after-uninstall.json"
actual_after_uninstall="$temporary_codex_home/actual-after-uninstall.json"

install -d -m 700 "$temporary_codex_home"
jq -n \
  --arg project_command "$project_command" \
  --arg other_command "$other_command" '
  {
    description: "ownership regression fixture",
    hooks: {
      PreCompact: [
        {
          matcher: "manual",
          hooks: [
            {
              type: "command",
              command: $other_command,
              timeout: 7,
              statusMessage: "Keep unrelated group"
            }
          ]
        },
        {
          matcher: "auto|manual",
          hooks: [
            {
              type: "command",
              command: "/usr/bin/true",
              timeout: 5,
              statusMessage: "Keep first sibling"
            },
            {
              type: "command",
              command: $project_command,
              timeout: 1,
              statusMessage: "Replace stale owned handler"
            },
            {
              type: "command",
              command: $other_command,
              timeout: 9,
              statusMessage: "Keep same-basename sibling"
            }
          ]
        }
      ],
      Stop: [
        {
          hooks: [
            {type: "command", command: "/usr/bin/true", timeout: 5},
            {type: "command", command: $project_command, timeout: 1},
            {type: "command", command: $other_command, timeout: 11}
          ]
        }
      ]
    }
  }
' >"$temporary_codex_home/hooks.json"
chmod 600 "$temporary_codex_home/hooks.json"

jq -n --arg other_command "$other_command" '
  {
    description: "ownership regression fixture",
    hooks: {
      PreCompact: [
        {
          matcher: "manual",
          hooks: [
            {
              type: "command",
              command: $other_command,
              timeout: 7,
              statusMessage: "Keep unrelated group"
            }
          ]
        },
        {
          matcher: "auto|manual",
          hooks: [
            {
              type: "command",
              command: "/usr/bin/true",
              timeout: 5,
              statusMessage: "Keep first sibling"
            },
            {
              type: "command",
              command: $other_command,
              timeout: 9,
              statusMessage: "Keep same-basename sibling"
            }
          ]
        }
      ],
      Stop: [
        {
          hooks: [
            {type: "command", command: "/usr/bin/true", timeout: 5},
            {type: "command", command: $other_command, timeout: 11}
          ]
        }
      ]
    }
  }
' >"$expected_after_uninstall"

assert_installed_shape() {
  jq -e \
    --arg project_command "$project_command" \
    --arg other_command "$other_command" '
    . as $root |
    ([
      "PreCompact",
      "PostCompact",
      "Stop",
      "SubagentStop",
      "SessionStart",
      "UserPromptSubmit"
    ] | all(
      . as $event |
      ([
        $root.hooks[$event][]?.hooks[]? |
        select((.command // "") == $project_command)
      ] | length) == 1
    )) and
    ([
      .. | objects |
      select((.command // "") == $other_command)
    ] | length) == 3 and
    ([
      .hooks.PreCompact[] |
      select(
        .matcher == "manual" and
        .hooks == [
          {
            type: "command",
            command: $other_command,
            timeout: 7,
            statusMessage: "Keep unrelated group"
          }
        ]
      )
    ] | length) == 1 and
    ([
      .hooks.PreCompact[] |
      select(
        .matcher == "auto|manual" and
        .hooks == [
          {
            type: "command",
            command: "/usr/bin/true",
            timeout: 5,
            statusMessage: "Keep first sibling"
          },
          {
            type: "command",
            command: $other_command,
            timeout: 9,
            statusMessage: "Keep same-basename sibling"
          }
        ]
      )
    ] | length) == 1 and
    ([
      .hooks.Stop[] |
      select(.hooks == [
        {type: "command", command: "/usr/bin/true", timeout: 5},
        {type: "command", command: $other_command, timeout: 11}
      ])
    ] | length) == 1
  ' "$temporary_codex_home/hooks.json" >/dev/null
}

CODEX_HOME="$temporary_codex_home" "$ROOT/scripts/install.sh"
assert_installed_shape
CODEX_COMPACTION_GUARD_EXECUTABLE="$project_command" \
  python3 -m unittest -v "$ROOT/tests/test_hook_lifecycle.py"

# Reinstallation must replace only the exact owned handlers and remain idempotent.
CODEX_HOME="$temporary_codex_home" "$ROOT/scripts/install.sh"
assert_installed_shape

CODEX_HOME="$temporary_codex_home" "$ROOT/scripts/uninstall.sh" --purge-state
if [[ -e "$project_command" ]]; then
  printf 'uninstall ownership test failed: binary still exists\n' >&2
  exit 1
fi

jq -S . "$temporary_codex_home/hooks.json" >"$actual_after_uninstall"
jq -S . "$expected_after_uninstall" >"$expected_after_uninstall.sorted"
if ! cmp -s "$expected_after_uninstall.sorted" "$actual_after_uninstall"; then
  diff -u "$expected_after_uninstall.sorted" "$actual_after_uninstall" >&2 || true
  printf 'uninstall ownership test failed: unrelated hook structure changed\n' >&2
  exit 1
fi

printf '%s\n' 'Hook ownership install, reinstall, and uninstall checks passed.'
