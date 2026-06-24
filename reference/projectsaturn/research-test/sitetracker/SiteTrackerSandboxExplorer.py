"""
SiteTracker Sandbox — Site Explorer & CRUD Test
================================================
Reads credentials from .env, authenticates to the SiteTracker (Salesforce)
sandbox, then:

  1. Finds a Site by its Name (e.g. "W047201")
  2. Fetches the Site object /describe to get field metadata (labels, types)
  3. Prints a table: Technical Field Name | Label | Type | Value
  4. Runs a full CRUD cycle: Create → Read → Update → Delete a test Site

Usage::

    .venv\\Scripts\\python.exe research-test\\sitetracker\\SiteTrackerSandboxExplorer.py
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


def headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


def soql_query(token: str, soql: str) -> dict:
    """Run a SOQL query and return the parsed JSON response."""
    url = f"{INSTANCE_URL}/services/data/{API_VERSION}/query?q={urlquote(soql, safe='+,')}"
    resp = requests.get(url, headers=headers(token))
    resp.raise_for_status()
    return resp.json()


def describe_sobject(token: str, sobject: str) -> dict:
    """GET /sobjects/<name>/describe → full field metadata."""
    url = f"{INSTANCE_URL}/services/data/{API_VERSION}/sobjects/{sobject}/describe"
    resp = requests.get(url, headers=headers(token))
    resp.raise_for_status()
    return resp.json()


def sobject_url(sobject: str, record_id: str = "") -> str:
    base = f"{INSTANCE_URL}/services/data/{API_VERSION}/sobjects/{sobject}"
    return f"{base}/{record_id}" if record_id else f"{base}/"


# ── Part 1: Find a site and display field metadata table ─────────────────────

def find_site_and_show_fields(token: str, site_name: str):
    """
    Find a Site by Name, fetch its full record, cross-reference with
    /describe metadata, and print a readable table.
    """
    print(f"\n{'='*80}")
    print(f"  PART 1 — Find site '{site_name}' and map fields to labels")
    print(f"{'='*80}")

    # Step 1: Get field metadata from /describe
    print("\n  Fetching sitetracker__Site__c describe metadata…")
    desc = describe_sobject(token, "sitetracker__Site__c")
    fields_meta = {f["name"]: f for f in desc["fields"]}
    field_names = sorted(fields_meta.keys())
    print(f"  Found {len(fields_meta)} fields in schema.")

    # Step 2: Build a SOQL query that selects ALL described fields
    # Site_ID__c holds the human-visible site code (e.g. "W047201"),
    # while Name holds the descriptive location name.
    soql = (
        f"SELECT {', '.join(field_names)} FROM sitetracker__Site__c "
        f"WHERE Site_ID__c = '{site_name}' OR Name LIKE '%{site_name}%' LIMIT 1"
    )
    print(f"\n  Running SOQL to find site '{site_name}' (by Site_ID__c or Name)…")
    result = soql_query(token, soql)

    if result["totalSize"] == 0:
        print(f"  Site '{site_name}' not found!")
        return

    record = result["records"][0]
    record_id = record["Id"]
    print(f"  Found! Record ID: {record_id}")

    # Step 3: Print table
    print(f"\n  {'Technical Field Name':<50} {'Label':<45} {'Type':<15} Value")
    print(f"  {'─'*50} {'─'*45} {'─'*15} {'─'*50}")

    for fname in field_names:
        meta = fields_meta[fname]
        label = meta.get("label", "")
        ftype = meta.get("type", "")
        value = record.get(fname)

        # Skip the SOQL attributes blob
        if fname == "attributes":
            continue

        # Truncate long values for display
        val_str = str(value) if value is not None else ""
        if len(val_str) > 60:
            val_str = val_str[:57] + "…"

        print(f"  {fname:<50} {label:<45} {ftype:<15} {val_str}")

    print(f"\n  Total fields shown: {len(field_names)}")

    # Step 4: Print picklist/multipicklist enum values
    picklist_fields = [
        (f["name"], f["label"], f["type"], f["picklistValues"])
        for f in desc["fields"]
        if f["type"] in ("picklist", "multipicklist") and f["picklistValues"]
    ]
    if picklist_fields:
        print(f"\n  {'='*80}")
        print(f"  Picklist / Multipicklist enum values")
        print(f"  {'='*80}")
        for fname, label, ftype, values in sorted(picklist_fields):
            active = [v for v in values if v.get("active")]
            print(f"\n  {fname} ({label}) [{ftype}, {len(active)} active values]:")
            for v in active:
                default = " (default)" if v.get("defaultValue") else ""
                print(f"    - {v['value']}{default}")

    return record_id


# ── Part 2: CRUD test ───────────────────────────────────────────────────────

SOBJECT = "sitetracker__Site__c"

# ── Writable field names (C+U) from /describe ────────────────────────────────
# Read-only / system fields (not createable or updateable):
#   Id, CreatedById, CreatedDate, IsDeleted, LastActivityDate,
#   LastModifiedById, LastModifiedDate, LastReferencedDate, LastViewedDate,
#   SystemModstamp, sitetracker__Location__c (compound from lat/lon)
#
# Calculated / formula fields (read-only, derived):
#   sitetracker__Full_Address__c  — formula from street + city + zip
#   sitetracker__Link_to_Map__c   — formula from lat/lon into Google Maps link
#
# Surprisingly writable:
#   sitetracker__Lat_DMS__c / sitetracker__Long_DMS__c — NOT calculated,
#   these are independent writable fields (not auto-derived from Lat/Long).
#
# Update method: PATCH only (PUT returns 405 Method Not Allowed).
# External ID lookup by Site_ID__c is NOT available (field is not marked
# as External ID in Salesforce), so we query by SOQL to get the SF Id.

def build_site_payload(site_id: str) -> dict:
    """
    Build a Site payload dict with realistic sample data.
    Used for both CREATE and UPDATE — edit values here before running.

    Only writable (createable + updateable) fields are included.
    Reference fields (lookup IDs) are omitted — set them if you have valid IDs.
    """
    return {
        # ── Identity ─────────────────────────────────────────────────────
        "Name":                                "CRUD Test — Torshov Garasje",
        "Site_ID__c":                          site_id,
        "Country__c":                          "Norway(NOR)",

        # ── Classification ───────────────────────────────────────────────
        "sitetracker__Site_Type__c":           "HOUSING_ASSOCIATION",
        "sitetracker__Site_Status__c":         "Planning started",
        "Owner_Type__c":                       "Caas",
        "Load_Management__c":                  "OCPP-DLM-1.6J",

        # ── EV equipment ─────────────────────────────────────────────────
        "EV_Charging_Level__c":                "Level 2 AC 22kWh",
        "EV_Connector_Type__c":                "Type 2",

        # ── Address ──────────────────────────────────────────────────────
        "sitetracker__Street_Address__c":      "Sandakerveien 58",
        "sitetracker__Street_Address_2__c":    "",
        "sitetracker__City__c":                "Oslo",
        "sitetracker__Zip_Code__c":            "0477",
        "sitetracker__County__c":              "Oslo",

        # ── Coordinates ──────────────────────────────────────────────────
        "sitetracker__Lat__c":                 "59.9423",
        "sitetracker__Long__c":                "10.7570",
        "sitetracker__Location__Latitude__s":  59.9423,
        "sitetracker__Location__Longitude__s": 10.7570,
        "sitetracker__Lat_DMS__c":             "59 56 32.28 N",
        "sitetracker__Long_DMS__c":            "10 45 25.20 E",

        # ── Description & dates ──────────────────────────────────────────
        "sitetracker__Site_Description__c":    "Underground parking, 12 spots",
        "Installed_Date__c":                   None,
        "Open_Date__c":                        None,

        # ── External links ───────────────────────────────────────────────
        "Client_reference_ID__c":              "",
        "Hubspot_Id__c":                       "",
        "Hubspot_Link__c":                     None,
        "Landing_Page__c":                     None,
        "Sharepoint_docs__c":                  None,

        # ── Flags ────────────────────────────────────────────────────────
        "sitetracker__Include_In_Site_Inquiries__c": False,

        # ── Reference / lookup fields (set to None — fill in valid IDs if needed)
        # "OwnerId":                    None,
        # "Price__c":                   None,
        # "Site2GridSupply__c":         None,
        # "SiteIC1__c":                 None,
        # "Site_Relation__c":           None,
        # "sitetracker__Customer__c":   None,
        # "sitetracker__Parent_Site__c":None,
        # "sitetracker__Territory__c":  None,
    }


# Overrides applied on UPDATE (merged on top of the base payload).
# Edit here to test changing specific fields.
UPDATE_OVERRIDES = {
    "Name":                             "CRUD Test — Torshov Garasje UPDATED",
    "sitetracker__Site_Status__c":      "Installation started",
    "sitetracker__Site_Description__c": "Underground parking, 12 spots — updated via API",
    "sitetracker__Street_Address__c":   "Sandakerveien 60",
}


def create_site(token: str, payload: dict) -> dict:
    """POST a new Site. Returns the Salesforce response (contains 'id')."""
    resp = requests.post(sobject_url(SOBJECT), headers=headers(token), json=payload)
    if resp.status_code not in (200, 201):
        print(f"  CREATE failed ({resp.status_code}): {resp.text}")
        sys.exit(1)
    return resp.json()


def read_site(token: str, record_id: str) -> dict:
    """GET a Site by Salesforce record Id. Returns full record."""
    resp = requests.get(sobject_url(SOBJECT, record_id), headers=headers(token))
    resp.raise_for_status()
    return resp.json()


def read_site_by_site_id(token: str, site_id: str) -> dict | None:
    """Find a Site by Site_ID__c via SOQL. Returns record or None."""
    result = soql_query(token, f"SELECT Id FROM {SOBJECT} WHERE Site_ID__c = '{site_id}' LIMIT 1")
    if result["totalSize"] == 0:
        return None
    return read_site(token, result["records"][0]["Id"])


def update_site(token: str, record_id: str, payload: dict):
    """PATCH a Site. Salesforce returns 204 on success."""
    resp = requests.patch(sobject_url(SOBJECT, record_id), headers=headers(token), json=payload)
    if resp.status_code != 204:
        print(f"  UPDATE failed ({resp.status_code}): {resp.text}")
        sys.exit(1)


def delete_site(token: str, record_id: str):
    """DELETE a Site. Salesforce returns 204 on success."""
    resp = requests.delete(sobject_url(SOBJECT, record_id), headers=headers(token))
    if resp.status_code != 204:
        print(f"  DELETE failed ({resp.status_code}): {resp.text}")
        sys.exit(1)


def verify_fields(record: dict, expected: dict) -> bool:
    """Compare record values against expected payload. Returns True if all match."""
    ok = True
    for key, expected_val in expected.items():
        actual_val = record.get(key)
        # Normalize: None vs empty string
        if expected_val is None and actual_val is None:
            continue
        if expected_val == "" and actual_val is None:
            continue  # Salesforce stores empty strings as null
        # Normalize: numeric strings — Salesforce may pad trailing zeros
        # e.g. "59.9423" vs "59.942300000000000"
        a_str = str(actual_val).strip() if actual_val is not None else ""
        e_str = str(expected_val).strip() if expected_val is not None else ""
        try:
            if float(a_str) == float(e_str):
                continue
        except (ValueError, TypeError):
            pass
        if a_str != e_str:
            print(f"    MISMATCH  {key}: expected={expected_val!r}  got={actual_val!r}")
            ok = False
    return ok


def crud_test(token: str):
    """
    Full CRUD cycle with verification:
      1. CREATE with sample payload
      2. READ + verify
      3. UPDATE (PATCH) with overrides
      4. READ + verify
      5. DELETE + confirm gone

    Pauses between state-changing steps so you can inspect in the UI.
    """
    timestamp = datetime.now().strftime("%H%M%S")
    site_id = f"WT{timestamp}"  # max 9 chars (e.g. "WT133430")

    print(f"\n{'='*80}")
    print(f"  PART 2 — CRUD Test (Create → Read → Update → Delete)")
    print(f"  Site ID: {site_id}")
    print(f"{'='*80}")

    create_payload = build_site_payload(site_id)

    # ── 1. CREATE ────────────────────────────────────────────────────────
    print(f"\n  [CREATE] Payload:")
    print(f"  {json.dumps(create_payload, indent=4, default=str)}")
    input("\n  Press Enter to CREATE…")

    result = create_site(token, create_payload)
    record_id = result["id"]
    print(f"  ✓ Created  Id={record_id}  Site_ID={site_id}")

    # ── 2. READ after create ─────────────────────────────────────────────
    print(f"\n  [READ] Fetching record by Id…")
    record = read_site(token, record_id)
    print(f"  ✓ Retrieved  Name={record.get('Name')}  Status={record.get('sitetracker__Site_Status__c')}")
    if verify_fields(record, create_payload):
        print(f"  ✓ All fields match expected create payload.")
    else:
        print(f"  ✗ Some fields differ — see MISMATCH lines above.")

    # ── 3. UPDATE ────────────────────────────────────────────────────────
    update_payload = {**create_payload, **UPDATE_OVERRIDES}
    print(f"\n  [UPDATE] Changed fields:")
    for key, val in UPDATE_OVERRIDES.items():
        print(f"    {key}: {create_payload.get(key)!r} → {val!r}")
    input("\n  Press Enter to UPDATE (PATCH)…")

    update_site(token, record_id, update_payload)
    print(f"  ✓ Updated (HTTP 204)")

    # ── 4. READ after update ─────────────────────────────────────────────
    print(f"\n  [READ] Verifying update…")
    record = read_site(token, record_id)
    print(f"  ✓ Retrieved  Name={record.get('Name')}  Status={record.get('sitetracker__Site_Status__c')}")
    if verify_fields(record, update_payload):
        print(f"  ✓ All fields match expected update payload.")
    else:
        print(f"  ✗ Some fields differ — see MISMATCH lines above.")

    # ── 5. DELETE ────────────────────────────────────────────────────────
    input("\n  Press Enter to DELETE…")
    delete_site(token, record_id)
    print(f"  ✓ Deleted (HTTP 204)")

    # Verify gone
    resp = requests.get(sobject_url(SOBJECT, record_id), headers=headers(token))
    if resp.status_code == 404:
        print(f"  ✓ Confirmed: record no longer exists (HTTP 404)")
    else:
        print(f"  ✗ Unexpected: GET returned {resp.status_code}")

    print(f"\n  CRUD test complete.")


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    print("=" * 80)
    print("  SiteTracker Sandbox Explorer")
    print(f"  Instance: {INSTANCE_URL}")
    print("=" * 80)

    token = authenticate()

    # Part 1: Find site and show metadata
    find_site_and_show_fields(token, "W047201")

    # Part 2: CRUD test
    print("\n")
    answer = input("Run CRUD test (create/read/update/delete a test site)? (y/n): ").strip().lower()
    if answer == "y":
        crud_test(token)
    else:
        print("Skipped CRUD test.")

    print("\nDone.")


if __name__ == "__main__":
    main()
