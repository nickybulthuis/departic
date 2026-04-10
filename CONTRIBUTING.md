# Contributing to Departic

Thanks for your interest in contributing! This is a hobby project, so
expectations are relaxed — but a few guidelines help keep things tidy.

## Getting started

```bash
git clone https://github.com/nickybulthuis/departic.git
cd departic
uv sync          # installs all dev dependencies
```

## Running checks locally

```bash
uv run ruff check           # lint
uv run ruff format --check  # format check
uv run pytest               # tests + coverage (95 % minimum)
```

Or let pre-commit handle it:

```bash
uv run pre-commit install
uv run pre-commit run --all-files
```

## Pull requests

1. Fork the repo and create a branch from `main`.
2. Make your changes.
3. Ensure `ruff check`, `ruff format --check`, and `pytest` all pass.
4. Open a PR with a clear description of what changed and why.

## Reporting issues

Open a [GitHub issue](https://github.com/nickybulthuis/departic/issues) with:

- What you expected vs. what happened
- Steps to reproduce
- Relevant logs or screenshots

## Code style

- Python 3.13+
- Formatted and linted by [Ruff](https://docs.astral.sh/ruff/)
- Tests live next to the code they test (`*_test.py`)

## License

By contributing you agree that your contributions will be licensed under the
[MIT License](LICENSE).
