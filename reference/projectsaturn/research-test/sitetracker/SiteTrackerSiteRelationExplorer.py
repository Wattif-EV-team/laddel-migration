"""
SiteTracker Sandbox — Site Relations & Company (Account) Explorer
================================================================
Research script to learn how Site Relations work:

  - Find or create a Company (Account) by Business Registration Number
  - Create a Site Relation linking a Site to a Company with a role
  - Full CRUD cycle on Site Relations
  - Deduplication of Companies by Business Registration Number

Object model::

    sitetracker__Site__c (Site)
        ← Site_Relation__c (junction)
            → Account (Company)

    Site_Relation__c fields (writable):
        Site__c                     reference → sitetracker__Site__c
        Company__c                  reference → Account
        Site_Relation_Role__c       picklist (OWNER of SITE, INSTALLER, etc.)
        Site_Relation_Start_Date__c date
        previous_CPO__c             string
        Grid_Supply__c              reference → Grid_Supply__c
        Name                        auto-number (read-only, e.g. "000784")

    Account required fields:
        Name                              string
        Business_Registration_Number__c   string (NOT external ID — dedup by SOQL)

Usage::

    .venv\\Scripts\\python.exe research-test\\sitetracker\\SiteTrackerSiteRelationExplorer.py
"""

import os
import sys
import json
from datetime import datetime
from urllib.parse import quote as urlquote

import requests
from dotenv import load_dotenv

# ── Load config from .env ────────────────────────────────────────────────────
load_dotenv()

TOKEN_URL     = os.getenv("SITETRACKER_TOKEN_URL")
INSTANCE_URL  = os.getenv("SITETRACKER_INSTANCE_URL")
CLIENT_ID     = os.getenv("SITETRACKER_CLIENT_ID")
CLIENT_SECRET = os.getenv("SITETRACKER_CLIENT_SECRET")
USERNAME      = os.getenv("SITETRACKER_USERNAME")
PASSWORD      = os.getenv("SITETRACKER_PASSWORD")

API_VERSION   = "v63.0"

for var_name in ("SITETRACKER_TOKEN_URL", "SITETRACKER_INSTANCE_URL",
                 "SITETRACKER_CLIENT_ID", "SITETRACKER_CLIENT_SECRET",
                 "SITETRACKER_USERNAME", "SITETRACKER_PASSWORD"):
    if not os.getenv(var_name):
        print(f"ERROR: {var_name} not set in .env")
        sys.exit(1)


# ── Helpers ──────────────────────────────────────────────────────────────────

def authenticate() -> str:
    """OAuth2 password grant → access token."""
    payload = {
        "grant_type":    "password",
        "client_id":     CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "username":      USERNAME,
        "password":      PASSWORD,
    }
    resp = requests.post(TOKEN_URL, data=payload)
    if resp.status_code != 200:
        print(f"Auth failed ({resp.status_code}): {resp.text}")
        sys.exit(1)
    token_data = resp.json()
    print(f"Authenticated as {USERNAME}")
    print(f"  Instance : {token_data.get('instance_url')}")
    print(f"  Token    : {token_data['access_token'][:25]}…")
    return token_data["access_token"]


def hdrs(token: str) -> dict:
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


def soql_query(token: str, soql: str) -> dict:
    url = f"{INSTANCE_URL}/services/data/{API_VERSION}/query?q={urlquote(soql, safe='+,')}"
    resp = requests.get(url, headers=hdrs(token))
    resp.raise_for_status()
    return resp.json()


def sobject_url(sobject: str, record_id: str = "") -> str:
    base = f"{INSTANCE_URL}/services/data/{API_VERSION}/sobjects/{sobject}"
    return f"{base}/{record_id}" if record_id else f"{base}/"


# ── Account (Company) helpers ────────────────────────────────────────────────

def find_account_by_reg_no(token: str, reg_no: str) -> dict | None:
    """Find an Account by Business Registration Number. Returns record or None."""
    soql = (
        f"SELECT Id, Name, Business_Registration_Number__c, Type, Phone "
        f"FROM Account "
        f"WHERE Business_Registration_Number__c = '{reg_no}' LIMIT 1"
    )
    result = soql_query(token, soql)
    if result["totalSize"] == 0:
        return None
    return result["records"][0]


def create_account(token: str, payload: dict) -> dict:
    """POST a new Account. Returns Salesforce response (contains 'id')."""
    resp = requests.post(sobject_url("Account"), headers=hdrs(token), json=payload)
    if resp.status_code not in (200, 201):
        print(f"  CREATE Account failed ({resp.status_code}): {resp.text}")
        sys.exit(1)
    return resp.json()


def delete_account(token: str, record_id: str):
    """DELETE an Account."""
    resp = requests.delete(sobject_url("Account", record_id), headers=hdrs(token))
    if resp.status_code != 204:
        print(f"  DELETE Account failed ({resp.status_code}): {resp.text}")
        sys.exit(1)


def find_or_create_account(token: str, payload: dict) -> tuple[str, bool]:
    """
    Find an Account by Business_Registration_Number__c, or create one.
    Returns (account_id, was_created).
    """
    reg_no = payload.get("Business_Registration_Number__c")
    if not reg_no:
        raise ValueError("Account payload must include Business_Registration_Number__c")

    existing = find_account_by_reg_no(token, reg_no)
    if existing:
        print(f"  Found existing Account: {existing['Name']} (Id={existing['Id']})")
        return existing["Id"], False

    print(f"  No Account with regNo={reg_no} — creating…")
    result = create_account(token, payload)
    print(f"  ✓ Created Account Id={result['id']}")
    return result["id"], True


# ── Site_Relation__c helpers ─────────────────────────────────────────────────

def find_site_id_by_code(token: str, site_code: str) -> str | None:
    """Find a Site's Salesforce Id by its Site_ID__c code."""
    result = soql_query(
        token,
        f"SELECT Id FROM sitetracker__Site__c WHERE Site_ID__c = '{site_code}' LIMIT 1"
    )
    if result["totalSize"] == 0:
        return None
    return result["records"][0]["Id"]


def list_site_relations(token: str, site_sf_id: str) -> list[dict]:
    """List all Site Relations for a given Site (by Salesforce Id)."""
    soql = (
        f"SELECT Id, Name, Site_Relation_Role__c, previous_CPO__c, "
        f"Site_Relation_Start_Date__c, "
        f"Company__c, Company__r.Name, Company__r.Business_Registration_Number__c "
        f"FROM Site_Relation__c "
        f"WHERE Site__c = '{site_sf_id}'"
    )
    return soql_query(token, soql).get("records", [])


def create_site_relation(token: str, payload: dict) -> dict:
    """POST a new Site_Relation__c. Returns Salesforce response."""
    resp = requests.post(sobject_url("Site_Relation__c"), headers=hdrs(token), json=payload)
    if resp.status_code not in (200, 201):
        print(f"  CREATE Site_Relation failed ({resp.status_code}): {resp.text}")
        sys.exit(1)
    return resp.json()


def read_site_relation(token: str, record_id: str) -> dict:
    """GET a Site_Relation__c by Salesforce Id."""
    resp = requests.get(sobject_url("Site_Relation__c", record_id), headers=hdrs(token))
    resp.raise_for_status()
    return resp.json()


def update_site_relation(token: str, record_id: str, payload: dict):
    """PATCH a Site_Relation__c."""
    resp = requests.patch(
        sobject_url("Site_Relation__c", record_id), headers=hdrs(token), json=payload
    )
    if resp.status_code != 204:
        print(f"  UPDATE Site_Relation failed ({resp.status_code}): {resp.text}")
        sys.exit(1)


def delete_site_relation(token: str, record_id: str):
    """DELETE a Site_Relation__c."""
    resp = requests.delete(sobject_url("Site_Relation__c", record_id), headers=hdrs(token))
    if resp.status_code != 204:
        print(f"  DELETE Site_Relation failed ({resp.status_code}): {resp.text}")
        sys.exit(1)


# ── Sample payloads ─────────────────────────────────────────────────────────

# Account (Company) payload — used for find_or_create_account
SAMPLE_ACCOUNT = {
    "Name":                              "ZTEST Borettslag AS",
    "Business_Registration_Number__c":   "999999901",
    "Type":                              "Customer",
    "Phone":                             "+47 999 99 901",
    "ShippingStreet":                    "Testveien 1",
    "ShippingCity":                      "Oslo",
    "ShippingPostalCode":                "0100",
    "ShippingCountry":                   "Norway",
}

# Site_Relation__c payload — Site__c and Company__c are set dynamically
SAMPLE_SITE_RELATION = {
    # "Site__c":      set at runtime (Salesforce Id of the Site)
    # "Company__c":   set at runtime (Salesforce Id of the Account)
    "Site_Relation_Role__c":       "OWNER of SITE",
    "Site_Relation_Start_Date__c": "2025-01-15",
    "previous_CPO__c":             "",
}

# Overrides for UPDATE step
RELATION_UPDATE_OVERRIDES = {
    "Site_Relation_Role__c": "INSTALLER",
    "previous_CPO__c":       "Test Previous CPO",
}


# ── Part 1: Browse existing Site Relations ───────────────────────────────────

def browse_site_relations(token: str, site_code: str):
    """Show all Site Relations for a given site code."""
    print(f"\n{'='*80}")
    print(f"  PART 1 — Browse Site Relations for '{site_code}'")
    print(f"{'='*80}")

    site_sf_id = find_site_id_by_code(token, site_code)
    if not site_sf_id:
        print(f"  Site '{site_code}' not found!")
        return

    print(f"  Site Salesforce Id: {site_sf_id}")
    relations = list_site_relations(token, site_sf_id)
    print(f"  Found {len(relations)} Site Relation(s):\n")

    print(f"  {'Auto#':<10} {'Role':<35} {'Company':<40} {'RegNo':<15} {'Start Date':<12} {'Prev CPO'}")
    print(f"  {'─'*10} {'─'*35} {'─'*40} {'─'*15} {'─'*12} {'─'*20}")
    for r in relations:
        co = r.get("Company__r") or {}
        print(
            f"  {r.get('Name', ''):<10} "
            f"{r.get('Site_Relation_Role__c', ''):<35} "
            f"{co.get('Name', ''):<40} "
            f"{co.get('Business_Registration_Number__c', ''):<15} "
            f"{r.get('Site_Relation_Start_Date__c') or '':<12} "
            f"{r.get('previous_CPO__c') or ''}"
        )

    # Also show available roles
    print(f"\n  Available Site_Relation_Role__c values:")
    desc = requests.get(
        f"{INSTANCE_URL}/services/data/{API_VERSION}/sobjects/Site_Relation__c/describe",
        headers=hdrs(token),
    ).json()
    role_field = next(f for f in desc["fields"] if f["name"] == "Site_Relation_Role__c")
    for v in role_field["picklistValues"]:
        if v.get("active"):
            dflt = " (default)" if v.get("defaultValue") else ""
            print(f"    - {v['value']}{dflt}")


# ── Part 2: CRUD test for Site Relation + Account ───────────────────────────

def crud_test(token: str, target_site_code: str):
    """
    Full CRUD cycle:
      1. Find or create a test Account (Company) by Business Registration Number
      2. Create a Site Relation linking the target Site to that Account
      3. Read + verify
      4. Update the relation (change role)
      5. Read + verify
      6. Delete the Site Relation
      7. Optionally delete the test Account

    Pauses between state-changing steps.
    """
    print(f"\n{'='*80}")
    print(f"  PART 2 — CRUD Test: Site Relation + Account")
    print(f"{'='*80}")

    # Resolve target Site
    site_sf_id = find_site_id_by_code(token, target_site_code)
    if not site_sf_id:
        print(f"  Site '{target_site_code}' not found!")
        return
    print(f"\n  Target Site: {target_site_code} → {site_sf_id}")

    # ── Step 1: Find or Create Account ───────────────────────────────────
    print(f"\n  [ACCOUNT] Find or create Company by regNo={SAMPLE_ACCOUNT['Business_Registration_Number__c']}")
    print(f"  Payload: {json.dumps(SAMPLE_ACCOUNT, indent=4)}")
    input("\n  Press Enter to find/create Account…")

    account_id, account_was_created = find_or_create_account(token, SAMPLE_ACCOUNT)

    # Verify dedup: calling again should find existing
    account_id_2, _ = find_or_create_account(token, SAMPLE_ACCOUNT)
    assert account_id == account_id_2, "Dedup failed — got different IDs!"
    print(f"  ✓ Dedup verified: same Account returned on second call.")

    # ── Step 2: Create Site Relation ─────────────────────────────────────
    relation_payload = {
        **SAMPLE_SITE_RELATION,
        "Site__c":    site_sf_id,
        "Company__c": account_id,
    }
    print(f"\n  [CREATE RELATION] Payload:")
    print(f"  {json.dumps(relation_payload, indent=4)}")
    input("\n  Press Enter to CREATE Site Relation…")

    result = create_site_relation(token, relation_payload)
    relation_id = result["id"]
    print(f"  ✓ Created Site_Relation__c Id={relation_id}")

    # ── Step 3: Read + verify ────────────────────────────────────────────
    print(f"\n  [READ] Fetching relation {relation_id}…")
    record = read_site_relation(token, relation_id)
    print(f"  ✓ Name={record.get('Name')}  Role={record.get('Site_Relation_Role__c')}")
    print(f"    Site__c={record.get('Site__c')}  Company__c={record.get('Company__c')}")
    print(f"    Start Date={record.get('Site_Relation_Start_Date__c')}")
    print(f"    previous_CPO__c={record.get('previous_CPO__c')}")

    # Verify it shows in the Site's relation list
    print(f"\n  Verifying relation appears in site's list…")
    all_rels = list_site_relations(token, site_sf_id)
    found = any(r["Id"] == relation_id for r in all_rels)
    print(f"  {'✓' if found else '✗'} Relation {'found' if found else 'NOT found'} in site's relation list.")

    # ── Step 4: Update (change role) ─────────────────────────────────────
    print(f"\n  [UPDATE] Changing fields:")
    for key, val in RELATION_UPDATE_OVERRIDES.items():
        print(f"    {key}: {relation_payload.get(key)!r} → {val!r}")
    input("\n  Press Enter to UPDATE (PATCH)…")

    update_site_relation(token, relation_id, RELATION_UPDATE_OVERRIDES)
    print(f"  ✓ Updated (HTTP 204)")

    # ── Step 5: Read + verify update ─────────────────────────────────────
    print(f"\n  [READ] Verifying update…")
    record = read_site_relation(token, relation_id)
    print(f"  ✓ Name={record.get('Name')}  Role={record.get('Site_Relation_Role__c')}")
    print(f"    previous_CPO__c={record.get('previous_CPO__c')}")
    for key, expected in RELATION_UPDATE_OVERRIDES.items():
        actual = record.get(key)
        if str(actual) != str(expected):
            print(f"    MISMATCH  {key}: expected={expected!r}  got={actual!r}")
        else:
            print(f"    ✓ {key} matches")

    # ── Step 6: Delete Site Relation ─────────────────────────────────────
    input("\n  Press Enter to DELETE Site Relation…")
    delete_site_relation(token, relation_id)
    print(f"  ✓ Deleted Site_Relation__c (HTTP 204)")

    # Verify gone from list
    all_rels = list_site_relations(token, site_sf_id)
    still_there = any(r["Id"] == relation_id for r in all_rels)
    print(f"  {'✗ Still in list!' if still_there else '✓ Confirmed removed from site relation list.'}")

    # ── Step 7: Clean up test Account ────────────────────────────────────
    if account_was_created:
        print(f"\n  [CLEANUP] Delete test Account {account_id}?")
        answer = input("  Delete? (y/n): ").strip().lower()
        if answer == "y":
            delete_account(token, account_id)
            print(f"  ✓ Deleted Account (HTTP 204)")
        else:
            print(f"  Kept Account {account_id} for further testing.")
    else:
        print(f"\n  Account was pre-existing — not deleting.")

    print(f"\n  CRUD test complete.")


# ── Main ─────────────────────────────────────────────────────────────────────

TARGET_SITE = "W047201"  # Adamstuen bygning 3

def main():
    print("=" * 80)
    print("  SiteTracker Sandbox — Site Relations Explorer")
    print(f"  Instance: {INSTANCE_URL}")
    print("=" * 80)

    token = authenticate()

    # Part 1: Browse existing relations
    browse_site_relations(token, TARGET_SITE)

    # Part 2: CRUD test
    print("\n")
    answer = input("Run CRUD test (Site Relation + Account)? (y/n): ").strip().lower()
    if answer == "y":
        crud_test(token, TARGET_SITE)
    else:
        print("Skipped CRUD test.")

    print("\nDone.")


if __name__ == "__main__":
    main()
