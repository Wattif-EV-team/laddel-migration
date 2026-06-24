"""
ResearchInstallDateBehavior.py
==============================
Research script to understand how SiteTracker's managed package
auto-manages the Install Date field on Field Assets when:
  - Status is changed (e.g., Decommissioned → Installed)
  - An Inventory Transaction is auto-created

Hypothesis: When status changes to "Installed", SiteTracker creates a
Consume/Install Inventory Transaction and sets Install Date = Transaction Date.

This script:
  1. Describes sitetracker__Inventory_Transaction__c (field metadata)
  2. Queries Inventory Transactions for a known Field Asset
  3. Tests: Change status from Installed → Decommissioned → Installed
     and observe Install Date + Inventory Transactions before/after

Uses SANDBOX credentials from .env.

Usage:
    .venv\\Scripts\\python.exe research-test\\sitetracker\\ResearchInstallDateBehavior.py
"""

import os
import sys
import json
import time
from datetime import datetime, date
from urllib.parse import quote as urlquote

import requests
from dotenv import load_dotenv

load_dotenv()

# ── Config ───────────────────────────────────────────────────────────────────
INSTANCE_URL = os.getenv("SITETRACKER_INSTANCE_URL")
API_VERSION = "v63.0"
OUTPUT_DIR = os.path.dirname(os.path.abspath(__file__))

SOBJECT_FA = "sitetracker__Field_Asset__c"
SOBJECT_IT = "sitetracker__Inventory_Transaction__c"


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
    data = resp.json()
    print(f"Authenticated as {os.getenv('SITETRACKER_USERNAME')}")
    print(f"  Instance: {data.get('instance_url')}")
    return data["access_token"]


def hdrs(token):
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


def soql_query(token, soql):
    """Run a SOQL query with pagination."""
    url = f"{INSTANCE_URL}/services/data/{API_VERSION}/query?q={urlquote(soql, safe='+,')}"
    resp = requests.get(url, headers=hdrs(token))
    resp.raise_for_status()
    result = resp.json()
    records = result.get("records", [])
    while not result.get("done", True):
        next_url = f"{INSTANCE_URL}{result['nextRecordsUrl']}"
        resp = requests.get(next_url, headers=hdrs(token))
        resp.raise_for_status()
        result = resp.json()
        records.extend(result.get("records", []))
    return records


def describe_sobject(token, sobject):
    """GET /sobjects/<name>/describe."""
    url = f"{INSTANCE_URL}/services/data/{API_VERSION}/sobjects/{sobject}/describe"
    resp = requests.get(url, headers=hdrs(token))
    resp.raise_for_status()
    return resp.json()


def read_record(token, sobject, record_id):
    """GET a single record."""
    url = f"{INSTANCE_URL}/services/data/{API_VERSION}/sobjects/{sobject}/{record_id}"
    resp = requests.get(url, headers=hdrs(token))
    resp.raise_for_status()
    return resp.json()


def update_record(token, sobject, record_id, payload):
    """PATCH a record."""
    url = f"{INSTANCE_URL}/services/data/{API_VERSION}/sobjects/{sobject}/{record_id}"
    resp = requests.patch(url, headers=hdrs(token), json=payload)
    if resp.status_code not in (200, 204):
        print(f"  UPDATE FAILED ({resp.status_code}): {resp.text}")
        return False
    return True


# ── Part 1: Describe Inventory Transaction ───────────────────────────────────

def part1_describe_inventory_transaction(token):
    """Describe Inventory Transaction object and save metadata."""
    print("\n" + "=" * 70)
    print("PART 1: Describe sitetracker__Inventory_Transaction__c")
    print("=" * 70)

    desc = describe_sobject(token, SOBJECT_IT)

    # Save full describe
    out_path = os.path.join(OUTPUT_DIR, "sitetracker_describe_Inventory_Transaction__c.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(desc, f, indent=2)
    print(f"  Saved describe to {out_path}")

    # Print key fields
    print(f"\n  Fields ({len(desc['fields'])} total):")
    print(f"  {'Name':<50} {'Label':<30} {'Type':<15} {'Updateable'}")
    print(f"  {'-'*50} {'-'*30} {'-'*15} {'-'*10}")
    for f_info in sorted(desc["fields"], key=lambda x: x["name"]):
        if f_info["name"].startswith("sitetracker__") or f_info["name"] in ("Name", "Id"):
            print(f"  {f_info['name']:<50} {f_info['label']:<30} {f_info['type']:<15} {f_info['updateable']}")

    # Check for 'Type' picklist values
    for f_info in desc["fields"]:
        if f_info["name"] == "sitetracker__Type__c" or "type" in f_info["name"].lower():
            if f_info.get("picklistValues"):
                print(f"\n  Picklist values for {f_info['name']}:")
                for pv in f_info["picklistValues"]:
                    if pv["active"]:
                        print(f"    - {pv['value']}")


# ── Part 2: Query Inventory Transactions for a specific Field Asset ──────────

def part2_query_inventory_transactions(token, field_asset_id):
    """Get all Inventory Transactions for a given Field Asset."""
    print("\n" + "=" * 70)
    print(f"PART 2: Inventory Transactions for Field Asset {field_asset_id}")
    print("=" * 70)

    soql = (
        f"SELECT Id, Name, sitetracker__Type__c, sitetracker__Quantity__c, "
        f"sitetracker__Transaction_Date__c, sitetracker__Field_Asset__c, "
        f"sitetracker__From_Site__c, sitetracker__To_Site__c, "
        f"sitetracker__Note__c, CreatedDate, CreatedById, LastModifiedDate "
        f"FROM {SOBJECT_IT} "
        f"WHERE sitetracker__Field_Asset__c = '{field_asset_id}' "
        f"ORDER BY CreatedDate DESC"
    )
    records = soql_query(token, soql)
    print(f"  Found {len(records)} transactions")

    for r in records:
        r.pop("attributes", None)
        print(f"  [{r.get('Name')}] Type={r.get('sitetracker__Type__c'):<20} "
              f"Date={r.get('sitetracker__Transaction_Date__c')} "
              f"Created={r.get('CreatedDate')} "
              f"Notes={r.get('sitetracker__Note__c', '')}")

    return records


# ── Part 3: Read Field Asset state (Install Date, Status, etc.) ──────────────

def part3_read_field_asset(token, field_asset_id):
    """Read current state of a Field Asset."""
    print(f"\n  Current Field Asset state ({field_asset_id}):")
    record = read_record(token, SOBJECT_FA, field_asset_id)
    key_fields = [
        "Name", "sitetracker__Status__c", "sitetracker__Install_Date__c",
        "sitetracker__Original_Install_Date__c", "sitetracker__Serial__c",
    ]
    for f_name in key_fields:
        print(f"    {f_name}: {record.get(f_name)}")
    return record


# ── Part 4: Status change experiment ─────────────────────────────────────────

def part4_status_change_experiment(token, field_asset_id):
    """
    Test what happens when we change status:
      1. Read current state
      2. Change to Decommissioned (if currently Installed) or vice versa
      3. Wait briefly, then read again
      4. Check if Install Date changed and if new Inventory Transaction appeared
      5. Revert status back

    WARNING: This modifies a record in the sandbox!
    """
    print("\n" + "=" * 70)
    print(f"PART 4: Status change experiment on {field_asset_id}")
    print("=" * 70)

    # Step 1: Read current state
    before = read_record(token, SOBJECT_FA, field_asset_id)
    current_status = before.get("sitetracker__Status__c")
    current_install_date = before.get("sitetracker__Install_Date__c")
    print(f"  Before: Status={current_status}, Install Date={current_install_date}")

    # Count existing transactions
    txn_before = soql_query(
        token,
        f"SELECT Id FROM {SOBJECT_IT} WHERE sitetracker__Field_Asset__c = '{field_asset_id}'"
    )
    print(f"  Inventory Transactions before: {len(txn_before)}")

    # Step 2: Toggle status
    if current_status == "Installed":
        new_status = "Decommissioned"
    elif current_status == "Decommissioned":
        new_status = "Installed"
    else:
        print(f"  Cannot experiment with status '{current_status}' — skipping.")
        return

    print(f"\n  Changing status: {current_status} → {new_status}...")
    ok = update_record(token, SOBJECT_FA, field_asset_id, {"sitetracker__Status__c": new_status})
    if not ok:
        return

    # Step 3: Wait and re-read
    print("  Waiting 3 seconds for triggers to fire...")
    time.sleep(3)

    after = read_record(token, SOBJECT_FA, field_asset_id)
    new_install_date = after.get("sitetracker__Install_Date__c")
    print(f"  After:  Status={after.get('sitetracker__Status__c')}, Install Date={new_install_date}")

    if new_install_date != current_install_date:
        print(f"  *** INSTALL DATE CHANGED: {current_install_date} → {new_install_date} ***")
    else:
        print(f"  Install Date unchanged: {current_install_date}")

    # Step 4: Check new transactions
    txn_after = soql_query(
        token,
        f"SELECT Id, Name, sitetracker__Type__c, sitetracker__Transaction_Date__c, "
        f"CreatedDate, sitetracker__Note__c "
        f"FROM {SOBJECT_IT} WHERE sitetracker__Field_Asset__c = '{field_asset_id}' "
        f"ORDER BY CreatedDate DESC"
    )
    new_txns = len(txn_after) - len(txn_before)
    print(f"  Inventory Transactions after: {len(txn_after)} (new: {new_txns})")
    if new_txns > 0:
        print("  New transactions:")
        for r in txn_after[:new_txns]:
            r.pop("attributes", None)
            print(f"    [{r.get('Name')}] Type={r.get('sitetracker__Type__c')} "
                  f"Date={r.get('sitetracker__Transaction_Date__c')} "
                  f"Created={r.get('CreatedDate')} "
                  f"Notes={r.get('sitetracker__Note__c', '')}")

    # Step 5: Now test if we can set Install Date AFTER the status change
    print(f"\n  Testing: Can we override Install Date after status change?")
    test_date = "2025-01-15"
    print(f"  Setting Install Date to {test_date}...")
    ok = update_record(token, SOBJECT_FA, field_asset_id, {"sitetracker__Install_Date__c": test_date})
    if ok:
        time.sleep(2)
        check = read_record(token, SOBJECT_FA, field_asset_id)
        actual_date = check.get("sitetracker__Install_Date__c")
        print(f"  Install Date after manual override: {actual_date}")
        if actual_date == test_date:
            print("  *** SUCCESS: Install Date can be overridden manually ***")
        else:
            print(f"  *** FAILED: Install Date was changed to {actual_date} (expected {test_date}) ***")

        # Check if another transaction was created just from changing Install Date
        txn_after_override = soql_query(
            token,
            f"SELECT Id FROM {SOBJECT_IT} WHERE sitetracker__Field_Asset__c = '{field_asset_id}'"
        )
        if len(txn_after_override) > len(txn_after):
            print(f"  *** Another Inventory Transaction was created just from changing Install Date! ***")
        else:
            print(f"  No new transactions from Install Date change alone.")

    # Step 6: Revert status back to original
    print(f"\n  Reverting status back to {current_status}...")
    ok = update_record(token, SOBJECT_FA, field_asset_id, {"sitetracker__Status__c": current_status})
    if ok:
        time.sleep(3)
        reverted = read_record(token, SOBJECT_FA, field_asset_id)
        reverted_date = reverted.get("sitetracker__Install_Date__c")
        print(f"  After revert: Status={reverted.get('sitetracker__Status__c')}, Install Date={reverted_date}")
        if reverted_date != test_date:
            print(f"  *** CONFIRMED: Status change overwrites Install Date! ***")
            print(f"    We set it to {test_date} but after status revert it became {reverted_date}")
        else:
            print(f"  Install Date stayed at our override value {test_date}")

    # Final transaction count
    txn_final = soql_query(
        token,
        f"SELECT Id, Name, sitetracker__Type__c, sitetracker__Transaction_Date__c, CreatedDate "
        f"FROM {SOBJECT_IT} WHERE sitetracker__Field_Asset__c = '{field_asset_id}' "
        f"ORDER BY CreatedDate DESC"
    )
    print(f"\n  Final transaction count: {len(txn_final)} (started with {len(txn_before)})")
    print("  All transactions:")
    for r in txn_final:
        r.pop("attributes", None)
        print(f"    [{r.get('Name')}] Type={r.get('sitetracker__Type__c'):<20} "
              f"Date={r.get('sitetracker__Transaction_Date__c')} Created={r.get('CreatedDate')}")


# ── Part 5: Test Install Date update WITHOUT status change ───────────────────

def part5_install_date_only(token, field_asset_id):
    """
    Test if changing ONLY Install Date (without touching Status) creates a transaction.
    """
    print("\n" + "=" * 70)
    print(f"PART 5: Install Date change WITHOUT status change on {field_asset_id}")
    print("=" * 70)

    before = read_record(token, SOBJECT_FA, field_asset_id)
    current_date = before.get("sitetracker__Install_Date__c")
    current_status = before.get("sitetracker__Status__c")
    print(f"  Before: Status={current_status}, Install Date={current_date}")

    txn_before = soql_query(
        token,
        f"SELECT Id FROM {SOBJECT_IT} WHERE sitetracker__Field_Asset__c = '{field_asset_id}'"
    )

    # Set a specific date
    test_date = "2024-06-15"
    print(f"  Setting Install Date to {test_date} (status unchanged)...")
    ok = update_record(token, SOBJECT_FA, field_asset_id, {"sitetracker__Install_Date__c": test_date})

    if ok:
        time.sleep(2)
        after = read_record(token, SOBJECT_FA, field_asset_id)
        actual_date = after.get("sitetracker__Install_Date__c")
        print(f"  After: Install Date={actual_date}")

        txn_after = soql_query(
            token,
            f"SELECT Id FROM {SOBJECT_IT} WHERE sitetracker__Field_Asset__c = '{field_asset_id}'"
        )
        new_txn_count = len(txn_after) - len(txn_before)
        print(f"  New transactions created: {new_txn_count}")
        if new_txn_count == 0:
            print("  *** CONFIRMED: Changing Install Date alone does NOT create transactions ***")
        else:
            print("  *** WARNING: Changing Install Date alone DID create transactions ***")

        # Restore original date
        if current_date:
            print(f"  Restoring original Install Date: {current_date}")
            update_record(token, SOBJECT_FA, field_asset_id, {"sitetracker__Install_Date__c": current_date})


# ── Part 6: Test combined Status + Install Date in single PATCH ──────────────

def part6_combined_update(token, field_asset_id):
    """
    Test if sending Status + Install Date in a single PATCH preserves our date
    or if the trigger overwrites it.
    """
    print("\n" + "=" * 70)
    print(f"PART 6: Combined Status + Install Date PATCH on {field_asset_id}")
    print("=" * 70)

    before = read_record(token, SOBJECT_FA, field_asset_id)
    current_status = before.get("sitetracker__Status__c")
    current_date = before.get("sitetracker__Install_Date__c")
    print(f"  Before: Status={current_status}, Install Date={current_date}")

    # Toggle status and set a specific date in same PATCH
    new_status = "Decommissioned" if current_status == "Installed" else "Installed"
    target_date = "2024-03-20"

    print(f"  PATCH: Status={new_status}, Install Date={target_date} (in same request)...")
    ok = update_record(token, SOBJECT_FA, field_asset_id, {
        "sitetracker__Status__c": new_status,
        "sitetracker__Install_Date__c": target_date,
    })

    if ok:
        time.sleep(3)
        after = read_record(token, SOBJECT_FA, field_asset_id)
        actual_date = after.get("sitetracker__Install_Date__c")
        actual_status = after.get("sitetracker__Status__c")
        print(f"  After: Status={actual_status}, Install Date={actual_date}")

        if actual_date == target_date:
            print(f"  *** Combined PATCH preserved our Install Date ***")
        else:
            print(f"  *** TRIGGER OVERWROTE: We sent {target_date} but got {actual_date} ***")

        # Revert
        print(f"  Reverting to original state...")
        update_record(token, SOBJECT_FA, field_asset_id, {
            "sitetracker__Status__c": current_status,
            "sitetracker__Install_Date__c": current_date or "",
        })
        time.sleep(2)


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    token = authenticate()

    # Part 1: Describe Inventory Transaction object
    part1_describe_inventory_transaction(token)

    # Find a Field Asset in sandbox to experiment with
    # Look for one with existing transactions
    print("\n" + "=" * 70)
    print("Finding a test Field Asset in sandbox...")
    print("=" * 70)

    # Find a field asset with "Installed" status
    test_assets = soql_query(
        token,
        f"SELECT Id, Name, sitetracker__Status__c, sitetracker__Install_Date__c, "
        f"sitetracker__Serial__c "
        f"FROM {SOBJECT_FA} "
        f"WHERE sitetracker__Status__c = 'Installed' "
        f"ORDER BY LastModifiedDate DESC LIMIT 5"
    )

    if not test_assets:
        print("No 'Installed' Field Assets found in sandbox. Trying any status...")
        test_assets = soql_query(
            token,
            f"SELECT Id, Name, sitetracker__Status__c, sitetracker__Install_Date__c "
            f"FROM {SOBJECT_FA} "
            f"ORDER BY LastModifiedDate DESC LIMIT 5"
        )

    if not test_assets:
        print("No Field Assets found in sandbox at all. Cannot proceed.")
        sys.exit(1)

    print(f"  Found {len(test_assets)} candidate assets:")
    for a in test_assets:
        a.pop("attributes", None)
        print(f"    {a['Name']} | Status={a.get('sitetracker__Status__c')} | "
              f"Install={a.get('sitetracker__Install_Date__c')} | Id={a['Id']}")

    # Use the first one for testing
    test_id = test_assets[0]["Id"]
    test_name = test_assets[0]["Name"]
    print(f"\n  Using: {test_name} ({test_id})")

    # Part 2: Show existing transactions
    part2_query_inventory_transactions(token, test_id)

    # Part 3: Current state
    part3_read_field_asset(token, test_id)

    # Part 4: Status change experiment
    print("\n  *** CAUTION: Parts 4-6 will modify the sandbox record! ***")
    proceed = "yes"  # Auto-proceed for sandbox testing
    if proceed != "yes":
        print("  Skipping experiments.")
        return

    part4_status_change_experiment(token, test_id)

    # Part 5: Install Date only (no status change)
    part5_install_date_only(token, test_id)

    # Part 6: Combined update
    part6_combined_update(token, test_id)

    print("\n" + "=" * 70)
    print("RESEARCH COMPLETE")
    print("=" * 70)
    print("""
CONCLUSIONS:
If Part 4 showed Install Date being overwritten after status change,
then SiteTracker's managed package has Apex triggers that:
  1. Create an Inventory Transaction when Status changes to Installed
  2. Set Install Date = Transaction Date (today)

If Part 5 showed no new transactions from Install Date change alone,
then the trigger is ONLY on Status changes, not on Install Date edits.

If Part 6 showed the combined PATCH date being overwritten, then the
trigger fires AFTER our PATCH completes and overwrites our value.

POSSIBLE WORKAROUNDS:
  A. Two-step approach: First change status, then in a separate call set Install Date
  B. Accept that Install Date will be today's date when status = Installed
  C. Only send Install Date if Status is NOT changing in the same operation
  D. Skip sending Install Date entirely and let SiteTracker manage it
""")


if __name__ == "__main__":
    main()
