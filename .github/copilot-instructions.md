# Copilot instructions — laddel-migration

Internal tool for migrating data from a source MySQL database (`laddel`) to a target
MySQL database via a `ladmig` CLI. Python 3.14, managed with `uv`.

## Project layout

```
src/laddel_migration/   # package (CLI, config, logging, db)
tests/                  # pytest suite
sql/                    # this project's SQL
reference/              # READ-ONLY reference from an earlier project (see below)
```

## Common commands

```powershell
uv sync                 # install deps
uv run ladmig --help    # CLI
uv run ruff check .      # lint
uv run ruff format .     # format
uv run pyright           # type check
uv run pytest            # tests
```

## `reference/` — read-only, never a dependency

The `reference/` folder holds files copied from an **earlier, unrelated migration
project** (`projectsaturn`). They exist **only for lookup and inspiration**.

**Hard rules:**

- ❌ NEVER `import` from `reference/` in `src/` or `tests/`.
- ❌ NEVER add `reference/` to `pyproject.toml`, the package, `sys.path`, or any
  build/test/runtime path.
- ❌ NEVER execute those scripts as part of this project.
- ✅ You MAY read them to understand patterns, entity relationships, Ampeco API call
  shapes, and SQL/view structure, then **re-implement equivalent logic from scratch**
  for this project's stack.

**Why care:** the earlier project migrated data out of a **different CSMS**, so its
**source schema is different** from our `laddel` source even though the domain
(EV charging) is the same. It also targeted PostgreSQL/SQL Server via `pyodbc`, whereas
this project uses MySQL — SQL dialect, types, and identifier quoting differ. Treat all
source-side details there as illustrative only, not authoritative for our schema.

See `reference/README.md` for details.

## Databases & schemas

Two MySQL databases, and we never blur the line between them:

- **`laddel`** — the **source** database. Read-only. All source data lives here; views
  reference it as `` `laddel`.`<table>` ``.
- **`target`** — the **writable** database. It holds **everything we produce**: the
  **target views** (Ampeco-shaped payloads), the **mapping tables** (target-system IDs +
  migration state), and any **reports** (analysis / quality views). Nothing we create
  lives anywhere but `target`.

## Migration database (`sql/`)

The migration is built as **views** in the writable `target` database that reshape the
read-only `laddel` source into Ampeco's shape. DDL lives in `sql/` as numbered files
(`3xx` = target views, one file per view). Full conventions are in `sql/README.md`.

```powershell
uv run ladmig build                       # run every sql/*.sql in numeric order
uv run ladmig build --file 301_target_charge_points.sql   # run a single file
uv run ladmig verify                      # COUNT(*) sanity check on key views
```

**Hard rules:**

- ❌ NEVER drop or write to the `laddel` source database — it is **read-only**. Views
  reference it as `` `laddel`.`<table>` ``.
- ❌ NEVER drop mapping tables (`0xx`, when they exist) — they hold target-system IDs and
  migration state. Use `CREATE TABLE IF NOT EXISTS` and guarded `ALTER TABLE`
  (MySQL 8 has no `ADD COLUMN IF NOT EXISTS`).
- ✅ View files (`1xx`–`4xx`) are drop-and-recreate and self-contained: `DROP VIEW IF
  EXISTS` then `CREATE OR REPLACE VIEW`, schema-qualified.
- ✅ One file per target view, named `3NN_target_<view_name>.sql`. Update `KEY_VIEWS`
  in `src/laddel_migration/cli.py` when adding/removing views.

## Inspecting the database & scratch scripts

**`uv run ladmig` is the way to do all project-specific database work** — building views,
verifying, and ad-hoc inspection. Do not hand-roll connection code; use the reusable CLI
helper:

```powershell
uv run ladmig sql "SHOW CREATE VIEW `charge_points`" --database target
uv run ladmig sql "DESCRIBE `charger`" --database source
uv run ladmig sql "SELECT * FROM `charger` LIMIT 5" --database source --csv
```

When the CLI isn't enough — multi-step exploration or one-off research chores — you MAY
write an **ephemeral throwaway** script in `scratch/` (git-ignored except its README).
Reuse `laddel_migration.config.load_settings` and `laddel_migration.db` helpers; never
import scratch scripts from package code. Anything you'd run more than a few times should
graduate into a real `ladmig` subcommand instead.
