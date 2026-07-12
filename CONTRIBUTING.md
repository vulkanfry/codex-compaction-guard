# Contributing

## Setup

```bash
cargo build
python3 -m unittest -v tests/test_hook_lifecycle.py
```

## Before submitting a change

```bash
./scripts/verify.sh
```

Behavior changes require:

- a focused Rust unit test when practical;
- an end-to-end lifecycle regression test;
- documentation updates for installation, trust, state, or output changes;
- a `CHANGELOG.md` entry.

Keep dependencies narrow and deterministic. The runtime hook must never call a
model or external network service.
