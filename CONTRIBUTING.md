# Contributing to Foreman

Thanks for considering a contribution. Foreman is a small experimental project, so focused changes
with clear motivation are the easiest to review.

## Before you start

- Search the existing issues before opening a new one.
- Open an issue before a large change so its scope and design can be discussed first.
- Never include API keys, `.env` files, or other credentials in an issue, commit, or test fixture.

Bug fixes, tests, documentation improvements, and small, well-scoped experiments are welcome.

## Development setup

Foreman requires Python 3.11 or newer. From a fresh checkout:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e '.[dev]'
```

The test suite is offline and does not need a TypeSafe API key, Codex login, or network access.

```bash
python -m pytest
python -m ruff check .
```

To run a single test while iterating:

```bash
python -m pytest tests/test_runtime.py
```

See [the runtime guide](docs/runtime.md) for the event flow and [the theory notes](docs/theory.md)
for the architecture behind it.

## Pull requests

Keep each pull request focused. Include:

- what changed and why;
- tests for behavior changes, or a short explanation when tests do not apply;
- documentation updates when commands, configuration, or behavior change; and
- confirmation that `pytest` and Ruff pass.

By contributing, you agree that your contribution is licensed under the repository's
[MIT License](LICENSE). All participants must follow the [Code of Conduct](CODE_OF_CONDUCT.md).
