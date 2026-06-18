# scratch/

Throwaway scripts and data for one-off database exploration and migration
chores. **Everything here except this README is git-ignored.**

Use this folder when a task needs more than a single ad-hoc query but does not
belong in the shipped CLI. Anything reusable should instead become a proper
`ladmig` command in `src/laddel_migration/`.

## When to use what

| Need | Use |
| ---- | --- |
| Quick look at schema/data | `uv run ladmig sql "<query>" --database source\|target` |
| Multi-step exploration, dumping many objects, ad-hoc reports | a throwaway script in `scratch/` |
| Something you'll run more than a few times | add a real `ladmig` subcommand |

## Pattern for a scratch script

Reuse the package's config and DB helpers so credentials come from `.env`:

```python
# scratch/explore.py  — run with:  uv run python scratch/explore.py
from laddel_migration.config import load_settings
from laddel_migration.db import run_query

settings = load_settings()
columns, rows = run_query(settings.target_db, "SHOW CREATE VIEW `users`")
for row in rows:
    print(row)
```

Keep scratch scripts disposable — do not import them from package code.
