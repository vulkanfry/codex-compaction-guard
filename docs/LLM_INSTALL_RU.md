# Инструкция для LLM-агента

Цель: установить Rust guard глобально для Codex, не затереть чужие hooks,
получить явное доверие пользователя и доказать работу на реальной compaction.

## Обязательный порядок

1. Прочитай `README.md`, `SECURITY.md`, `docs/LLM_INSTALL.md` и installer.
2. Проверь `codex`, Rust, Cargo и `jq`.
3. Запусти `./scripts/verify.sh`.
4. Установи через `./scripts/install.sh`.
5. Не меняй `remote_compaction_v2` и другие посторонние feature-флаги Codex:
   это отдельная пользовательская конфигурация.
6. Открой новую Codex-сессию и попроси пользователя проверить и доверить восемь
   определений через `/hooks`; у всех восьми должно быть `Active = 1`, а
   `Review` должен быть равен нулю.
7. Проверь установленный бинарник lifecycle-тестами.
8. Выполни одну реальную compaction в новой тестовой задаче и сопоставь её
   timestamp с checkpoint/audit.

## Запрещено

- полностью перезаписывать существующий `hooks.json`;
- придумывать или копировать старые `trusted_hash`;
- оставлять `--dangerously-bypass-hook-trust` как постоянную настройку;
- публиковать содержимое checkpoint и приватный код;
- утверждать, что сработал Rust, только потому что путь настроен.

## Как доказать Rust-инъекцию

Для нужной сессии должны одновременно существовать:

- свежий `compacted` event;
- `checkpoint.json` с `schema_version: 2` и непустым `checkpoint_id`;
- audit `checkpoint_saved` и `restore_armed` с совпадающим turn/timestamp;
- после доставки — ровно один новый `consumed-*.json`;
- модель получила текст про `additional local compaction` и `PAST steps`.

Если `pending.json` ещё существует, Rust `PreCompact/PostCompact` уже сработали,
но инъекция пока не была потреблена. Она произойдёт на первом hook-eligible
`PreToolUse` того же turn (обычный случай для авто-compaction), либо на Bash
`PostToolUse` после завершения `write_stdin`, либо на `Stop`,
`SubagentStop`, `SessionStart` или `UserPromptSubmit`.

Верхнеуровневый code-mode `functions.exec` сам не является lifecycle boundary.
Его подходящие вложенные вызовы, например `tools.exec_command`, проходят hooks
под каноническим именем `Bash`; `functions.wait` не вызывает tool-use hooks.

## Финальный отчёт

Отдельно укажи:

- Registered — бинарник и восемь hooks найдены.
- Trusted — текущие определения доверены.
- Real action worked — подтверждена реальная compaction и доставка enrichment.
- Not verified — что осталось недоказанным.
