# Security

## Trust model

This is a non-managed command hook. Codex should not run it until the user has
reviewed and trusted the exact current hook definition through `/hooks`.

Never distribute instructions that permanently use
`--dangerously-bypass-hook-trust`.

## Local data

Checkpoints may contain private conversation, source-code, diff, and proof-log
context. State is stored under:

```text
$CODEX_HOME/compaction-guard/
```

Directories are mode `0700`; files are mode `0600`. Do not upload checkpoint or
audit contents to bug reports without reviewing and redacting them.

## Redaction

The guard redacts common API-key, bearer-token, password, GitHub, Slack, AWS,
JWT, and private-key patterns. It excludes common credential files and secret
directories. Redaction is defense in depth, not proof that arbitrary project
text contains no secret.

## Reporting a vulnerability

Until a public security contact is configured, report vulnerabilities privately
to the repository owner. Do not open a public issue containing secrets,
checkpoint data, or an exploit that exposes local files.
