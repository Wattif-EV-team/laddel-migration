"""
ResearchFieldAssets.py
======================
Research script to explore Field Assets, Site Inventory, and Items in SiteTracker.

Downloads:
  1. Full /describe for sitetracker__Field_Asset__c
  2. Full /describe for sitetracker__Site_Inventory__c
  3. Full /describe for sitetracker__Item__c
  4. All Field Asset records with all fields
  5. All Items referenced by Field Assets
  6. All Site Inventory records

Saves JSON outputs into research-test/sitetracker/ for analysis.

Usage:
    .venv\\Scripts\\python.exe research-test\\sitetracker\\ResearchFieldAssets.py
"""

import os
import sys
import json
from collections import Counter, defaultdict
from urllib.parse import quote as urlquote

import requests
from dotenv import load_dotenv

load_dotenv()

# ── Config ───────────────────────────────────────────────────────────────────
INSTANCE_URL = os.getenv("SITETRACKER_INSTANCE_URL")
API_VERSION = "v63.0"
OUTPUT_DIR = os.path.dirname(os.path.abspath(__file__))


def authenticate():
    """OAuth2 password grant → access token."""
    token_url = os.getenv("SITETRACKER_TOKEN_URL")
    payload = {
        "grant_type": "password",
        "client_id": os.getenv("SITETRACKER_CLIENT_ID"),
        "client_secret": os.getenv("SITETRACKER_CLIENT_SECRET"),
        "username": os.getenv("SITETRACKER_USERNAME"),
        "password": os.getenv("SITETRACKER_PASSWORD"),
    }
    resp = requests.post(token_url, data=payload)
    if resp.status_code != 200:
        print(f"Auth failed ({resp.status_code}): {resp.text}")
        sys.exit(1)
    token = resp.json()["access_token"]
    print(f"Authenticated successfully")
    return token


def headers(token):
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


def soql_query(token, soql):
    """Run a SOQL query with pagination."""
    url = f"{INSTANCE_URL}/services/data/{API_VERSION}/query?q={urlquote(soql, safe='+,')}"
    resp = requests.get(url, headers=headers(token))
    resp.raise_for_status()
    result = resp.json()
    records = result.get("records", [])

    while not result.get("done", True):
        next_url = f"{INSTANCE_URL}{result['nextRecordsUrl']}"
        resp = requests.get(next_url, headers=headers(token))
        resp.raise_for_status()
        result = resp.json()
        records.extend(result.get("records", []))

    return records


def describe_sobject(token, sobject):
    """GET /sobjects/<name>/describe → full field metadata."""
    url = f"{INSTANCE_URL}/services/data/{API_VERSION}/sobjects/{sobject}/describe"
    resp = requests.get(url, headers=headers(token))
    resp.raise_for_status()
    return resp.json()


def save_json(filename, data):
    """Save data to JSON file in output dir."""
    path = os.path.join(OUTPUT_DIR, filename)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False, default=str)
    print(f"  Saved: {filename}")


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    token = authenticate()

    # ── 1. Describe objects ──────────────────────────────────────────────────
    print("\n" + "=" * 70)
    print("  PART 1: Describe Field Asset, Site Inventory, and Item objects")
    print("=" * 70)

    print("\n  Fetching sitetracker__Field_Asset__c describe...")
    fa_desc = describe_sobject(token, "sitetracker__Field_Asset__c")
    save_json("sitetracker_describe_Field_Asset__c.json", fa_desc)

    print("  Fetching sitetracker__Site_Inventory__c describe...")
    si_desc = describe_sobject(token, "sitetracker__Site_Inventory__c")
    save_json("sitetracker_describe_Site_Inventory__c.json", si_desc)

    print("  Fetching sitetracker__Item__c describe...")
    item_desc = describe_sobject(token, "sitetracker__Item__c")
    save_json("sitetracker_describe_Item__c.json", item_desc)

    # ── 2. Download all Field Assets ────────────────────────────────────────
    print("\n" + "=" * 70)
    print("  PART 2: Download all Field Asset records")
    print("=" * 70)

    # Get queryable field names (skip compound fields which can't be selected directly)
    fa_fields = [
        f["name"] for f in fa_desc["fields"]
        if f["type"] != "address"  # compound address fields
        and not f.get("compoundFieldName")  # sub-fields of compound
    ]
    # Remove compound geolocation parent if present
    fa_fields = [f for f in fa_fields if not (f.endswith("__c") and any(
        sub["compoundFieldName"] == f for sub in fa_desc["fields"]
    ))]

    # Actually let's be safe: just select all fields that are not compound parents
    queryable_fa_fields = []
    compound_parents = set()
    for f in fa_desc["fields"]:
        if f.get("compoundFieldName"):
            compound_parents.add(f["compoundFieldName"])
    for f in fa_desc["fields"]:
        if f["name"] not in compound_parents and f["type"] != "location":
            queryable_fa_fields.append(f["name"])

    print(f"  Querying {len(queryable_fa_fields)} fields...")
    fa_soql = f"SELECT {', '.join(queryable_fa_fields)} FROM sitetracker__Field_Asset__c"
    fa_records = soql_query(token, fa_soql)
    print(f"  Downloaded {len(fa_records)} Field Asset records")
    save_json("sitetracker_field_assets_all.json", fa_records)

    # ── 3. Download all Items referenced ────────────────────────────────────
    print("\n" + "=" * 70)
    print("  PART 3: Download Items linked to Field Assets")
    print("=" * 70)

    # Find the Item reference field on Field Asset
    item_ref_fields = [
        f for f in fa_desc["fields"]
        if f.get("referenceTo") and "sitetracker__Item__c" in f["referenceTo"]
    ]
    print(f"  Item reference fields on Field Asset: {[f['name'] for f in item_ref_fields]}")

    # Collect unique Item IDs from Field Assets
    item_ids = set()
    for rec in fa_records:
        for ref_field in item_ref_fields:
            val = rec.get(ref_field["name"])
            if val:
                item_ids.add(val)

    print(f"  Found {len(item_ids)} unique Items referenced by Field Assets")

    if item_ids:
        # Get queryable Item fields
        queryable_item_fields = []
        item_compound_parents = set()
        for f in item_desc["fields"]:
            if f.get("compoundFieldName"):
                item_compound_parents.add(f["compoundFieldName"])
        for f in item_desc["fields"]:
            if f["name"] not in item_compound_parents and f["type"] != "location":
                queryable_item_fields.append(f["name"])

        # Query items in batches (SOQL IN clause limit)
        item_records = []
        item_id_list = list(item_ids)
        batch_size = 200
        for i in range(0, len(item_id_list), batch_size):
            batch = item_id_list[i:i + batch_size]
            ids_str = "', '".join(batch)
            item_soql = f"SELECT {', '.join(queryable_item_fields)} FROM sitetracker__Item__c WHERE Id IN ('{ids_str}')"
            batch_records = soql_query(token, item_soql)
            item_records.extend(batch_records)
            print(f"    Batch {i // batch_size + 1}: {len(batch_records)} items")

        print(f"  Downloaded {len(item_records)} Item records total")
        save_json("sitetracker_items_for_field_assets.json", item_records)
    else:
        print("  No Items found; trying to download all Items directly...")
        queryable_item_fields = []
        item_compound_parents = set()
        for f in item_desc["fields"]:
            if f.get("compoundFieldName"):
                item_compound_parents.add(f["compoundFieldName"])
        for f in item_desc["fields"]:
            if f["name"] not in item_compound_parents and f["type"] != "location":
                queryable_item_fields.append(f["name"])
        item_soql = f"SELECT {', '.join(queryable_item_fields)} FROM sitetracker__Item__c"
        item_records = soql_query(token, item_soql)
        print(f"  Downloaded {len(item_records)} Item records total")
        save_json("sitetracker_items_all.json", item_records)

    # ── 4. Download all Site Inventory records ──────────────────────────────
    print("\n" + "=" * 70)
    print("  PART 4: Download all Site Inventory records")
    print("=" * 70)

    queryable_si_fields = []
    si_compound_parents = set()
    for f in si_desc["fields"]:
        if f.get("compoundFieldName"):
            si_compound_parents.add(f["compoundFieldName"])
    for f in si_desc["fields"]:
        if f["name"] not in si_compound_parents and f["type"] != "location":
            queryable_si_fields.append(f["name"])

    print(f"  Querying {len(queryable_si_fields)} fields...")
    si_soql = f"SELECT {', '.join(queryable_si_fields)} FROM sitetracker__Site_Inventory__c"
    si_records = soql_query(token, si_soql)
    print(f"  Downloaded {len(si_records)} Site Inventory records")
    save_json("sitetracker_site_inventory_all.json", si_records)

    # ── 5. Analysis Summary ─────────────────────────────────────────────────
    print("\n" + "=" * 70)
    print("  PART 5: Analysis Summary")
    print("=" * 70)

    # Field Asset analysis
    print("\n  --- Field Asset Schema Summary ---")
    print(f"  Total fields: {len(fa_desc['fields'])}")

    # Required fields
    required_fa = [
        f for f in fa_desc["fields"]
        if not f["nillable"] and f["createable"] and not f.get("defaultedOnCreate")
    ]
    print(f"\n  REQUIRED fields (not nillable, createable, no default):")
    for f in required_fa:
        print(f"    {f['name']:<50} {f['label']:<40} {f['type']}")

    # Createable fields
    createable_fa = [f for f in fa_desc["fields"] if f["createable"]]
    print(f"\n  Createable fields: {len(createable_fa)}")

    # Reference/lookup fields
    ref_fa = [f for f in fa_desc["fields"] if f["type"] == "reference"]
    print(f"\n  Reference (lookup) fields:")
    for f in ref_fa:
        print(f"    {f['name']:<50} → {f['referenceTo']}")

    # Field population analysis on actual data
    print(f"\n  --- Field Population Analysis (on {len(fa_records)} records) ---")
    field_pop = Counter()
    for rec in fa_records:
        for key, val in rec.items():
            if key == "attributes":
                continue
            if val is not None and val != "":
                field_pop[key] += 1

    print(f"\n  Fields with data (sorted by population %):")
    print(f"  {'Field':<50} {'Count':<8} {'%':<6} Label")
    print(f"  {'─'*50} {'─'*8} {'─'*6} {'─'*40}")
    fa_field_meta = {f["name"]: f for f in fa_desc["fields"]}
    for field_name, count in field_pop.most_common():
        pct = (count / len(fa_records) * 100) if fa_records else 0
        label = fa_field_meta.get(field_name, {}).get("label", "")
        print(f"  {field_name:<50} {count:<8} {pct:>5.1f}% {label}")

    # Sample values for key fields
    print(f"\n  --- Sample values (first 5 unique per populated field) ---")
    for field_name, count in field_pop.most_common():
        if count == 0:
            continue
        meta = fa_field_meta.get(field_name)
        if not meta or not meta.get("createable"):
            continue
        # Skip ID/system fields
        if field_name in ("Id", "OwnerId", "CreatedById", "LastModifiedById", "attributes"):
            continue
        values = set()
        for rec in fa_records:
            v = rec.get(field_name)
            if v is not None and v != "":
                values.add(str(v)[:80])
            if len(values) >= 5:
                break
        if values:
            print(f"\n  {field_name} ({meta['label']}, {meta['type']}):")
            for v in sorted(values):
                print(f"    • {v}")

    # Site Inventory analysis
    print(f"\n\n  --- Site Inventory Schema Summary ---")
    print(f"  Total fields: {len(si_desc['fields'])}")

    required_si = [
        f for f in si_desc["fields"]
        if not f["nillable"] and f["createable"] and not f.get("defaultedOnCreate")
    ]
    print(f"\n  REQUIRED fields (not nillable, createable, no default):")
    for f in required_si:
        print(f"    {f['name']:<50} {f['label']:<40} {f['type']}")

    ref_si = [f for f in si_desc["fields"] if f["type"] == "reference"]
    print(f"\n  Reference (lookup) fields:")
    for f in ref_si:
        print(f"    {f['name']:<50} → {f['referenceTo']}")

    # Site Inventory population analysis
    if si_records:
        print(f"\n  --- Site Inventory Field Population (on {len(si_records)} records) ---")
        si_pop = Counter()
        for rec in si_records:
            for key, val in rec.items():
                if key == "attributes":
                    continue
                if val is not None and val != "":
                    si_pop[key] += 1

        si_field_meta = {f["name"]: f for f in si_desc["fields"]}
        print(f"  {'Field':<50} {'Count':<8} {'%':<6} Label")
        print(f"  {'─'*50} {'─'*8} {'─'*6} {'─'*40}")
        for field_name, count in si_pop.most_common():
            pct = (count / len(si_records) * 100)
            label = si_field_meta.get(field_name, {}).get("label", "")
            print(f"  {field_name:<50} {count:<8} {pct:>5.1f}% {label}")

    # How Field Assets link to Sites
    print(f"\n\n  --- How Field Asset links to Site ---")
    site_ref_on_fa = [
        f for f in fa_desc["fields"]
        if f["type"] == "reference" and "sitetracker__Site__c" in (f.get("referenceTo") or [])
    ]
    if site_ref_on_fa:
        for f in site_ref_on_fa:
            print(f"  DIRECT: {f['name']} ({f['label']}) → sitetracker__Site__c")
    else:
        print("  No direct Site lookup on Field Asset")
        # Check if it links through Site Inventory
        si_ref_on_fa = [
            f for f in fa_desc["fields"]
            if f["type"] == "reference" and "sitetracker__Site_Inventory__c" in (f.get("referenceTo") or [])
        ]
        if si_ref_on_fa:
            for f in si_ref_on_fa:
                print(f"  VIA SITE INVENTORY: {f['name']} ({f['label']}) → sitetracker__Site_Inventory__c")

    # Check Site Inventory link to Site
    print(f"\n  --- How Site Inventory links to Site ---")
    site_ref_on_si = [
        f for f in si_desc["fields"]
        if f["type"] == "reference" and "sitetracker__Site__c" in (f.get("referenceTo") or [])
    ]
    if site_ref_on_si:
        for f in site_ref_on_si:
            print(f"  DIRECT: {f['name']} ({f['label']}) → sitetracker__Site__c")

    # Item analysis
    print(f"\n\n  --- Item Schema Summary ---")
    print(f"  Total fields: {len(item_desc['fields'])}")
    createable_item = [f for f in item_desc["fields"] if f["createable"]]
    print(f"  Createable fields: {len(createable_item)}")
    for f in createable_item:
        print(f"    {f['name']:<50} {f['label']:<40} {f['type']}")

    # Check creation dates for Field Assets
    print(f"\n\n  --- Field Asset Creation Date Distribution ---")
    from collections import defaultdict
    date_counts = defaultdict(int)
    for rec in fa_records:
        cd = rec.get("CreatedDate", "")
        if cd:
            date_counts[cd[:10]] += 1  # YYYY-MM-DD
    for dt in sorted(date_counts.keys()):
        print(f"    {dt}: {date_counts[dt]} records")

    print("\n\nDone! Check JSON files for full data.")


if __name__ == "__main__":
    main()
