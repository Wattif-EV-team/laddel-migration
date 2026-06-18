# laddel-migration

Internal tool for migrating data from a source MySQL database to a target MySQL database.

Exposes a `ladmig` CLI.

## Requirements

- [uv](https://docs.astral.sh/uv/) for environment and dependency management
- Python 3.14 (installed automatically by uv from `.python-version`)

## Setup

```powershell
uv sync
```

This creates `.venv/` and installs runtime and dev dependencies.

Copy the environment template and fill in real credentials:

```powershell
Copy-Item .env.example .env
```

`.env` is git-ignored and holds the MySQL host, port, user, password and database names.

## Usage

```powershell
uv run ladmig --help        # show all commands
uv run ladmig status        # print version / readiness
uv run ladmig test          # check connectivity to source and target databases
```

`ladmig test` connects to both the `source` and `target` databases (configured via
`DB_SOURCE_NAME` / `DB_TARGET_NAME`) and runs `SELECT 1` against each. It exits
non-zero if any connection fails.

## Configuration

All runtime configuration comes from environment variables (optionally loaded from a
local `.env` file). See `.env.example` for the full list. No configuration lives in
`pyproject.toml`, and config discovery does not depend on the current working directory.

| Variable         | Description                       | Default  |
| ---------------- | --------------------------------- | -------- |
| `DB_HOST`        | MySQL host                        | required |
| `DB_PORT`        | MySQL port                        | `3306`   |
| `DB_USER`        | MySQL user                        | required |
| `DB_PASSWORD`    | MySQL password                    | required |
| `DB_SOURCE_NAME` | Source database name              | `laddel` |
| `DB_TARGET_NAME` | Target database name              | `target` |

## Development

```powershell
uv run ruff check .          # lint
uv run ruff format .         # format
uv run pyright               # type check
uv run pytest                # tests
```

The same checks run on every pull request via GitHub Actions (`.github/workflows/ci.yml`).

## Project layout

```
src/laddel_migration/   # package (CLI, config, logging, db)
tests/                  # pytest suite
```
