"""
CreateOrUpdateSiteTrackerFieldAssets.py
=======================================
Create or update Field Assets in SiteTracker (Salesforce).

Reads from Target.SiteTrackerFieldAssets view and:
  - If TargetSfFieldAssetId is not NULL → update existing Field Asset
  - If TargetSfFieldAssetId is NULL → SOQL lookup by Serial+Item, then create
  - On create: writes mapping via INSERT + rowcount check (SystemExit on failure)

The target view follows the 3-section pattern:
  - Source columns (mapping_table, mapping_key, project_code, source_label)
  - Target IDs (TargetSfFieldAssetId — NULL if not yet created)
  - Payload (SF API field names used 1:1 for the API call)

At startup, resolves all distinct Item Names from the target view to SF Item IDs
via SOQL — rows referencing unknown Items are warned and skipped (not abort).

Two-pass processing:
  - Pass 1: Create/update all field assets. Parent references (sitetracker__Parent__c)
    that are NULL are simply omitted from the payload.
  - Pass 2: Re-query view. For rows whose parent reference changed from NULL to a value
    (because the parent was just created in pass 1), PATCH only the parent field.

Usage:
    .venv\\Scripts\\python.exe CreateOrUpdateSiteTrackerFieldAssets.py
"""

import sys
import json
import logging
import datetime

import pyodbc
from dotenv import load_dotenv

from utils.log_utils import setup_logging
from utils.config_utils import get_db_connection_string
from utils.dbutils import get_db_connection, get_sql_dialect, quote_identifier
from utils.sitetracker_utils import (
    get_sitetracker_token,
    sitetracker_soql_query,
    sitetracker_soql_query_all,
    sitetracker_create,
    sitetracker_update,
    sitetracker_read,
    snapshot_record,
    log_field_diffs,
    escape_soql,
)

load_dotenv()

# Constants
SOBJECT = "sitetracker__Field_Asset__c"

# Payload columns: all columns from the target view's Payload section.
# These are used 1:1 as SF API field names in the payload.
PAYLOAD_COLUMNS = [
    "Name",
    "sitetracker__Item__c",
    "sitetracker__Site__c",
    "sitetracker__Status__c",
    "sitetracker__Serial__c",
    "sitetracker__Install_Date__c",
    "sitetracker__Original_Install_Date__c",
    "Ownership__c",
    "Location__c",
    "sitetracker__Notes__c",
    "MAC__c",
    "IMEI__c",
    "Password__c",
    "Factory_Default_Password__c",
    "iccID__c",
    "IP_Address__c",
    "URL_Management__c",
    "sitetracker__Parent__c",
]

# Counters
total_rows = 0
created_count = 0
updated_count = 0
skipped_count = 0
error_count = 0
parents_set_count = 0
errors = []

# Item cache: Name → SF Item ID (resolved at startup)
item_cache = {}


def fetch_target_field_assets(conn_str):
    """Fetch all rows from Target.SiteTrackerFieldAssets.

    Returns an empty list and logs a warning if the view does not exist
    (PostgreSQL SQLSTATE 42P01), allowing the caller to skip gracefully.
    Returns an empty list with info log if the view exists but has 0 rows.
    """
    try:
        with get_db_connection(conn_str) as conn:
            cursor = conn.cursor()
            dialect = get_sql_dialect(conn)
            target_schema = quote_identifier("Target", dialect)
            table = quote_identifier("SiteTrackerFieldAssets", dialect)
            cursor.execute(f"SELECT * FROM {target_schema}.{table}")
            rows = cursor.fetchall()
            if not rows:
                logging.info("No field assets to create or update.")
                return []
            return rows
    except pyodbc.ProgrammingError as e:
        if e.args and e.args[0] == "42P01":
            logging.warning("View Target.SiteTrackerFieldAssets does not exist — skipping.")
            return []
        raise


def resolve_item_ids(rows):
    """Resolve all distinct Item Names to SF Item IDs. Warns and skips missing Items."""
    item_names = set()
    for row in rows:
        item_name = getattr(row, "sitetracker__Item__c", None)
        if item_name:
            item_names.add(item_name)

    if not item_names:
        logging.error("No Item Names found in target view — check ChargerProductLookup mapping.")
        return False

    logging.info(f"Resolving {len(item_names)} distinct Item names to SF IDs...")

    # Query all Items in one SOQL call
    records = sitetracker_soql_query_all(
        "SELECT Id, Name FROM sitetracker__Item__c"
    )

    # Build lookup: Name → Id
    all_items = {r["Name"]: r["Id"] for r in records}

    missing = []
    for name in sorted(item_names):
        if name in all_items:
            item_cache[name] = all_items[name]
            logging.info(f"  Item '{name}' → {all_items[name]}")
        else:
            missing.append(name)
            logging.warning(f"  Item '{name}' NOT FOUND in SiteTracker — rows with this Item will be skipped")

    if missing:
        logging.warning(
            f"{len(missing)} Item(s) not found in SiteTracker: {missing}. "
            f"Field assets referencing these items will be skipped."
        )

    if not item_cache:
        logging.error("Zero Items could be resolved — nothing to process.")
        return False

    logging.info(f"Resolved {len(item_cache)} of {len(item_names)} Items successfully.")
    return True


def update_field_asset_mapping(conn_str, row, sf_field_asset_id, existed_before, snapshot):
    """Write the SF Field Asset ID into the mapping table. Hard abort if write fails."""
    label = getattr(row, "source_label", row.mapping_key)

    # Machine-readable log BEFORE INSERT (enables manual recovery if crash after SF create)
    logging.info(f"MAPPING_RECORD|mapping_key={row.mapping_key}|sf_id={sf_field_asset_id}")

    try:
        with get_db_connection(conn_str) as conn:
            with conn.cursor() as cursor:
                query = '''
                    INSERT INTO "Mapping"."sitetracker_field_asset_mapping"
                        (mapping_key, target_sf_field_asset_id,
                         asset_existed_before_migration, previous_record_snapshot)
                    VALUES (?, ?, ?, ?)
                '''
                params = (
                    row.mapping_key,
                    sf_field_asset_id,
                    existed_before,
                    json.dumps(snapshot) if snapshot else None,
                )
                cursor.execute(query, params)
                if cursor.rowcount == 0:
                    raise SystemExit(
                        f"Mapping INSERT affected 0 rows for {row.mapping_key}; "
                        f"halting to prevent orphaned Field Asset in SiteTracker."
                    )
                conn.commit()
                logging.info(
                    f"    Mapped {label} → {sf_field_asset_id} "
                    f"(existed_before={existed_before})"
                )
    except SystemExit:
        raise
    except Exception as e:
        raise SystemExit(
            f"Mapping write failed for {row.mapping_key} (sf_id={sf_field_asset_id}): {e} — "
            f"halting to prevent orphaned resources."
        )


def find_field_asset_by_serial(serial, item_sf_id=None):
    """Find an existing Field Asset by Serial, optionally validating Item type.

    Returns:
      - record dict if exactly one match with matching Item (or no item_sf_id provided)
      - None if no matches (create new)
      - "SKIP" string if multiple matches or Item type mismatch (caller should skip)
    """
    if not serial:
        return None
    soql = (
        f"SELECT Id, Name, sitetracker__Serial__c, sitetracker__Site__c, "
        f"sitetracker__Status__c, sitetracker__Item__c "
        f"FROM {SOBJECT} "
        f"WHERE sitetracker__Serial__c = '{escape_soql(serial)}'"
    )
    result = sitetracker_soql_query(soql)
    if result["totalSize"] == 0:
        return None

    records = result["records"]
    if len(records) > 1:
        logging.warning(f"    Multiple field assets found with serial '{serial}' — skipping")
        return "SKIP"

    record = records[0]
    if item_sf_id and record.get("sitetracker__Item__c") != item_sf_id:
        logging.warning(
            f"    Found asset with serial '{serial}' but different Item type "
            f"(expected={item_sf_id}, found={record.get('sitetracker__Item__c')}) — skipping"
        )
        return "SKIP"

    return record


def build_field_asset_payload(row):
    """Build the Salesforce payload from payload columns in the target view row.

    Uses PAYLOAD_COLUMNS list to read values 1:1 from the view.
    Special handling:
      - sitetracker__Item__c: resolves Item Name → SF Item ID
      - date/datetime fields: serialized to ISO string
      - None/empty values: excluded from payload

    Returns None if the Item cannot be resolved (caller should skip).
    """
    payload = {}

    for col in PAYLOAD_COLUMNS:
        value = getattr(row, col, None)

        # Skip None values
        if value is None:
            continue

        # Special case: Item name → resolve to SF ID
        if col == "sitetracker__Item__c":
            item_id = item_cache.get(value)
            if not item_id:
                return None  # Item not resolvable — caller should skip
            payload[col] = item_id
            continue

        # Date fields: serialize to ISO string
        if isinstance(value, (datetime.date, datetime.datetime)):
            payload[col] = str(value)
            continue

        # Strip whitespace from all string values to avoid false diffs
        if isinstance(value, str):
            value = value.strip()
            if value == "" and col != "Name":
                continue

        payload[col] = value

    return payload


def _verify_install_date(payload, sf_id, label):
    """Read back the record and re-send Install Date if Apex overwrote it.

    SiteTracker's Apex trigger can overwrite Install Date (e.g. when status
    is set to Installed). This fires after both create and update to ensure
    our intended value sticks.
    """
    install_date = payload.get("sitetracker__Install_Date__c")
    if not install_date:
        return
    after = sitetracker_read(SOBJECT, sf_id)
    actual = after.get("sitetracker__Install_Date__c")
    if str(actual) != str(install_date):
        logging.info(f"    Re-setting Install Date to {install_date} (Apex set it to {actual})")
        sitetracker_update(SOBJECT, sf_id, {"sitetracker__Install_Date__c": install_date})


def process_field_asset(conn_str, row):
    """Process a single field asset row: create or update in SiteTracker."""
    global created_count, updated_count, skipped_count, error_count

    label = getattr(row, "source_label", None) or f"{row.project_code}/{row.mapping_key}"
    sf_id = row.TargetSfFieldAssetId

    # Skip rows without Item mapping (NULL sitetracker_item_name in lookup)
    item_name = getattr(row, "sitetracker__Item__c", None)
    if not item_name:
        skipped_count += 1
        logging.warning(f"  SKIPPED {label}: no Item Name in view")
        return

    # Skip rows where Item Name could not be resolved to a SF ID
    if item_name not in item_cache:
        skipped_count += 1
        logging.warning(f"  SKIPPED {label}: Item '{item_name}' not found in SiteTracker")
        return

    item_sf_id = item_cache[item_name]

    try:
        if sf_id is not None:
            # Already mapped → snapshot + diff + update
            before = snapshot_record(SOBJECT, sf_id)
            payload = build_field_asset_payload(row)
            if payload is None:
                skipped_count += 1
                return
            log_field_diffs(label, before, payload)
            sitetracker_update(SOBJECT, sf_id, payload)
            _verify_install_date(payload, sf_id, label)
            updated_count += 1
            logging.info(f"  UPDATED {label} → {sf_id}")
        else:
            # Not mapped — SOQL lookup by Serial + Item type
            serial = getattr(row, "sitetracker__Serial__c", None)
            existing_record = find_field_asset_by_serial(serial, item_sf_id)

            if existing_record == "SKIP":
                skipped_count += 1
                logging.warning(f"  SKIPPED {label}: serial lookup conflict")
                return

            if existing_record:
                # Found in SiteTracker but not in our mapping
                sf_id = existing_record["Id"]
                logging.warning(f"  Field Asset already exists: {label} (serial={serial}) → {sf_id}")

                before = snapshot_record(SOBJECT, sf_id)
                payload = build_field_asset_payload(row)
                if payload is None:
                    skipped_count += 1
                    return
                log_field_diffs(label, before, payload)
                sitetracker_update(SOBJECT, sf_id, payload)
                _verify_install_date(payload, sf_id, label)

                # Write mapping (hard abort if fails)
                update_field_asset_mapping(conn_str, row, sf_id, True, before)
                updated_count += 1
                logging.info(f"  UPDATED (pre-existing) {label} → {sf_id}")
                return

            # Create new Field Asset
            payload = build_field_asset_payload(row)
            if payload is None:
                skipped_count += 1
                return
            result = sitetracker_create(SOBJECT, payload)
            sf_id = result["id"]
            _verify_install_date(payload, sf_id, label)

            # Write mapping (hard abort if this fails)
            update_field_asset_mapping(conn_str, row, sf_id, False, None)
            created_count += 1
            logging.info(f"  CREATED {label} → {sf_id}")

    except SystemExit:
        raise  # Let mapping failures propagate as hard aborts
    except Exception as e:
        error_count += 1
        errors.append(f"{label}: {e}")
        logging.error(f"  ERROR processing {label}: {e}")


def run_pass2_parent_references(conn_str, pass1_parent_values):
    """Pass 2: Re-query view and PATCH parent references that became available.

    After pass 1 creates parent assets, the view's JOIN to the mapping table
    resolves their SF IDs. This pass PATCHes sitetracker__Parent__c on child
    records whose parent was NULL in pass 1 but now has a value.
    """
    global parents_set_count

    rows = fetch_target_field_assets(conn_str)
    if not rows:
        return

    patched = 0
    for row in rows:
        parent_value = getattr(row, "sitetracker__Parent__c", None)
        sf_id = row.TargetSfFieldAssetId
        if not sf_id:
            continue  # Not yet created — skip

        # Check if parent changed from NULL to a value
        old_parent = pass1_parent_values.get(row.mapping_key)
        if old_parent is None and parent_value is not None:
            try:
                sitetracker_update(SOBJECT, sf_id, {"sitetracker__Parent__c": parent_value})
                patched += 1
                label = getattr(row, "source_label", row.mapping_key)
                logging.info(f"  SET PARENT {label} → {parent_value}")
            except Exception as e:
                label = getattr(row, "source_label", row.mapping_key)
                logging.error(f"  ERROR setting parent on {label}: {e}")

    parents_set_count = patched
    if patched > 0:
        logging.info(f"Pass 2: Set parent reference on {patched} field asset(s)")
    else:
        logging.info("Pass 2: No new parent references to set")


def main():
    """Main entry point."""
    global total_rows

    conn_str = get_db_connection_string()

    logging.info("=" * 70)
    logging.info("SiteTracker Field Assets — Create or Update")
    logging.info("=" * 70)

    # Authenticate
    get_sitetracker_token()

    # Fetch target data (returns [] if view missing or empty)
    rows = fetch_target_field_assets(conn_str)
    if not rows:
        return

    total_rows = len(rows)
    logging.info(f"Found {total_rows} field assets to process")

    # Resolve Item Names → SF IDs (returns False if zero resolved)
    if not resolve_item_ids(rows):
        return

    # Pass 1: Create/update all field assets
    # Track parent values sent in pass 1 for pass 2 comparison
    pass1_parent_values = {}
    for row in rows:
        pass1_parent_values[row.mapping_key] = getattr(row, "sitetracker__Parent__c", None)

    logging.info("")
    logging.info("Pass 1: Creating/updating field assets...")
    for i, row in enumerate(rows, 1):
        if i % 50 == 0:
            logging.info(f"Progress: {i}/{total_rows}")
        process_field_asset(conn_str, row)

    # Pass 2: Set parent references that became available after pass 1
    logging.info("")
    logging.info("Pass 2: Checking for newly-resolved parent references...")
    run_pass2_parent_references(conn_str, pass1_parent_values)

    # Summary
    logging.info("")
    logging.info("=" * 70)
    logging.info(f"COMPLETE: {total_rows} field assets processed")
    logging.info(f"  Created:      {created_count}")
    logging.info(f"  Updated:      {updated_count}")
    logging.info(f"  Skipped:      {skipped_count}")
    logging.info(f"  Parents set:  {parents_set_count}")
    logging.info(f"  Errors:       {error_count}")
    if errors:
        logging.info("  Error details:")
        for err in errors:
            logging.error(f"    {err}")
    logging.info("=" * 70)

    if error_count > 0:
        sys.exit(1)


if __name__ == "__main__":
    setup_logging("CreateOrUpdateSiteTrackerFieldAssets")
    try:
        main()
    except SystemExit as e:
        logging.critical(f"ABORT: {e}")
        sys.exit(1)
    except Exception as e:
        logging.error(f"An error occurred: {e}")
        sys.exit(1)
