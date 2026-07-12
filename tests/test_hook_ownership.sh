#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
temporary_codex_home="$(mktemp -d "${TMPDIR:-/tmp}/codex-compaction-guard-ownership.XXXXXX")"
trap 'rm -rf "$temporary_codex_home"' EXIT

project_command="$temporary_codex_home/hooks/compaction_guard"
other_command="/opt/other/compaction_guard"
fake_bin="$temporary_codex_home/fake-bin"
fake_codex_log="$temporary_codex_home/fake-codex.log"
expected_after_uninstall="$temporary_codex_home/expected-after-uninstall.json"
actual_after_uninstall="$temporary_codex_home/actual-after-uninstall.json"

install -d -m 700 "$temporary_codex_home" "$fake_bin"
fake_codex="$fake_bin/codex"
printf '%s\n' \
  '#!/usr/bin/env bash' \
  'set -euo pipefail' \
  'if [[ "$1" == "features" && "$2" == "enable" ]]; then' \
  '  printf "%s\\n" "$3" >>"$CODEX_FAKE_LOG"' \
  '  exit 0' \
  'fi' \
  'exit 2' >"$fake_codex"
chmod 755 "$fake_codex"

run_installer() {
  PATH="$fake_bin:$PATH" \
    CODEX_FAKE_LOG="$fake_codex_log" \
    CODEX_HOME="$temporary_codex_home" \
    "$ROOT/scripts/install.sh"
}

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
      PreToolUse: [
        {
          matcher: "exec_command",
          hooks: [
            {
              type: "command",
              command: $other_command,
              timeout: 3,
              statusMessage: "Keep unrelated tool hook"
            }
          ]
        }
      ],
      PostToolUse: [
        {
          matcher: "Bash",
          hooks: [
            {
              type: "command",
              command: $other_command,
              timeout: 4,
              statusMessage: "Keep unrelated post-tool hook"
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
      PreToolUse: [
        {
          matcher: "exec_command",
          hooks: [
            {
              type: "command",
              command: $other_command,
              timeout: 3,
              statusMessage: "Keep unrelated tool hook"
            }
          ]
        }
      ],
      PostToolUse: [
        {
          matcher: "Bash",
          hooks: [
            {
              type: "command",
              command: $other_command,
              timeout: 4,
              statusMessage: "Keep unrelated post-tool hook"
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
      "PreToolUse",
      "PostToolUse",
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
    ] | length) == 5 and
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
      .hooks.PreToolUse[] |
      select(
        .matcher == "exec_command" and
        .hooks == [
          {
            type: "command",
            command: $other_command,
            timeout: 3,
            statusMessage: "Keep unrelated tool hook"
          }
        ]
      )
    ] | length) == 1 and
    ([
      .hooks.PostToolUse[] |
      select(
        .matcher == "Bash" and
        .hooks == [
          {
            type: "command",
            command: $other_command,
            timeout: 4,
            statusMessage: "Keep unrelated post-tool hook"
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

assert_owned_matchers() {
  jq -e \
    --arg project_command "$project_command" '
    . as $root |
    [
      {event: "PreCompact", matcher: "auto|manual"},
      {event: "PostCompact", matcher: "auto|manual"},
      {event: "PreToolUse", matcher: null},
      {event: "PostToolUse", matcher: "^Bash$"},
      {event: "Stop", matcher: null},
      {event: "SubagentStop", matcher: null},
      {event: "SessionStart", matcher: "compact|resume"},
      {event: "UserPromptSubmit", matcher: null}
    ] | all(
      . as $expected |
      ([
        $root.hooks[$expected.event][]? |
        select(any(.hooks[]?; (.command // "") == $project_command)) |
        (.matcher // null)
      ] == [$expected.matcher])
    )
  ' "$temporary_codex_home/hooks.json" >/dev/null
}

assert_release_contract() {
  local expected='codex-compaction-guard 0.3.1 (schema 3)'
  local actual
  actual="$($project_command --version)"
  if [[ "$actual" != "$expected" ]]; then
    printf 'release contract mismatch: expected %s, got %s\n' "$expected" "$actual" >&2
    exit 1
  fi
}

run_installer
assert_installed_shape
assert_owned_matchers
assert_release_contract
if [[ "$(grep -c '^hooks$' "$fake_codex_log")" -ne 1 ]] ||
   [[ "$(wc -l <"$fake_codex_log" | tr -d ' ')" -ne 1 ]]; then
  printf 'feature enable regression failed: installer must enable only hooks\n' >&2
  exit 1
fi
CODEX_COMPACTION_GUARD_EXECUTABLE="$project_command" \
  python3 -m unittest -v "$ROOT/tests/test_hook_lifecycle.py"

# Reinstallation must replace only the exact owned handlers and remain idempotent.
run_installer
assert_installed_shape
assert_owned_matchers
assert_release_contract
if [[ "$(grep -c '^hooks$' "$fake_codex_log")" -ne 2 ]] ||
   [[ "$(wc -l <"$fake_codex_log" | tr -d ' ')" -ne 2 ]]; then
  printf 'feature enable regression failed after reinstall: only hooks is allowed\n' >&2
  exit 1
fi

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
