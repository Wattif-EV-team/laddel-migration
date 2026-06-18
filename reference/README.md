# Reference — Read-Only

This folder contains files copied from an **earlier, unrelated migration project**
(`projectsaturn`). They are kept here purely as **read-only reference material** for
lookup and inspiration.

> ⚠️ **These files MUST NEVER be imported, packaged, executed, or used as a library by
> `laddel-migration`.** They are not part of this project's source, dependency graph,
> build, or test suite.

## Why these files are here

The earlier project solved a conceptually similar problem (migrating EV-charging data
into the Ampeco platform). Reading how it modeled entities, structured DDL/SQL views,
and called the Ampeco API can save time. The *domain context* is the same; the
*implementation* is not directly reusable.

## Critical difference: different source schema

`projectsaturn` migrated data out of a **different CSMS** (Charging Station Management
System). Its **source database schema is different** from this project's source
(`laddel` on MySQL). Anything describing source tables, columns, or extraction logic in
this folder describes *that other system* and does **not** map 1:1 onto our source.

Treat the source-side details as illustrative only. The target side (Ampeco concepts:
partners, locations, charging zones, charge points, EVSEs, connectors, tariffs, etc.)
overlaps conceptually but may differ in specifics and platform version.

Additional notes:

- The original project targeted **PostgreSQL/SQL Server (pyodbc)**; this project uses
  **MySQL**. SQL dialect, types, and identifier quoting differ.
- Imports like `from utils.dbutils import ...` or `from utils.ampeco_utils import ...`
  refer to *this folder's* copies and are **not** available to `laddel-migration`.

## How to use this folder

- ✅ Read it to understand patterns, naming, entity relationships, and API call shapes.
- ✅ Re-implement equivalent logic from scratch against our own schema and stack.
- ❌ Do **not** `import` from `reference/` in `src/` or `tests/`.
- ❌ Do **not** add `reference/` to `pyproject.toml`, the package, or `sys.path`.
- ❌ Do **not** run these scripts as part of this project.

## Contents

```
projectsaturn/
  RunAllMigrationsSteps.py          # main migration runner (orchestrates the steps)
  BuildProjectSleetDatabase.py      # builds the staging DB from DDL files
  Fetch*.py / CreateOrUpdate*.py    # the step modules referenced by the runner
  utils/                            # shared helpers: db, Ampeco API, logging, config, etc.
  db/project-sleet/                 # ordered .sql DDL/view files + their README
```
