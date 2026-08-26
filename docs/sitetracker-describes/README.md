# SiteTracker describe snapshots (live `laddel` org)

Raw Salesforce `describe` metadata fetched from the **live laddel SiteTracker org**.
These files are the **evidence** behind every ✅ *"confirmed live"* claim in
[../sitetracker-reference.md](../sitetracker-reference.md) and the
[../fieldmapping/](../fieldmapping/) docs — they are tracked in git precisely so those
claims stay verifiable.

| File | sObject | Fetched |
|------|---------|---------|
| `sitetracker_describe_sitetracker__Site__c.json` | `sitetracker__Site__c` | 2026-08-13 |
| `sitetracker_describe_Site_Relation__c.json` | `Site_Relation__c` | 2026-08-13 |
| `sitetracker_describe_Account.json` | `Account` | 2026-08-13 |

## Refreshing

```powershell
uv run ladmig sitetracker describe sitetracker__Site__c --diff --save
uv run ladmig sitetracker describe Site_Relation__c --diff --save
uv run ladmig sitetracker describe Account --diff --save
```

Each invocation fetches the live describe, prints a NEW / REMOVED / CHANGED field diff
against **the previously saved snapshot in this folder** (drift since the last refresh —
not the one-time Wattif-sandbox bootstrap comparison the old `scratch/` script used), and
then overwrites the file here with the fresh JSON.

Commit the refreshed JSON together with whatever doc changes the diff justifies, and
date-stamp the claim in the doc — otherwise a later reader cannot tell which org or which
day a ✅ refers to.

## Which snapshot is authoritative?

- **These files** — the live `laddel` org. Authoritative for `sitetracker__Site__c`,
  `Site_Relation__c` and `Account`.
- **`reference/projectsaturn/research-test/sitetracker/`** — the older **Wattif sandbox**
  describes, copied from an unrelated earlier project. Read-only, kept only as a
  historical baseline (the one-time bootstrap diff when this folder was first
  populated). `--diff` no longer compares against it — it compares against whatever
  snapshot is already saved here, to detect drift since the last refresh. Not
  authoritative for laddel, and never a dependency (see
  [reference/README.md](../../reference/README.md)).
