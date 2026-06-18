"""
CreateOrUpdateSiteTrackerAccounts.py
=====================================
Phase 3: Create or update Accounts in SiteTracker (Salesforce).

Reads from Target.SiteTrackerAccounts view (which resolves mapping via JOIN) and:
  - If TargetSfAccountId is not NULL → update existing Account
  - If TargetSfAccountId is NULL → SOQL lookup by org_number, then by name, then create
  - On create: writes mapping via INSERT + rowcount check (SystemExit on failure)

Usage:
    .venv\\Scripts\\python.exe CreateOrUpdateSiteTrackerAccounts.py
"""

import sys
import json
import logging

from dotenv import load_dotenv

from utils.log_utils import setup_logging
from utils.config_utils import get_db_connection_string
from utils.dbutils import get_db_connection
from utils.sitetracker_utils import (
    get_sitetracker_token,
    find_account_by_org_number,
    find_account_by_name,
    normalize_org_number,
    sitetracker_create,
    sitetracker_update,
    snapshot_record,
    log_field_diffs,
)

load_dotenv()

# Constants
SOBJECT = "Account"

# Counters
total_rows = 0
created_count = 0
updated_count = 0
error_count = 0
errors = []


def fetch_target_accounts(conn_str):
    """Fetch all rows from Target.SiteTrackerAccounts."""
    with get_db_connection(conn_str) as conn:
        with conn.cursor() as cursor:
            cursor.execute('SELECT * FROM "Target"."SiteTrackerAccounts"')
            rows = cursor.fetchall()
            if not rows:
                raise SystemExit("Target.SiteTrackerAccounts returned 0 rows — aborting to prevent issues.")
            return rows


def update_account_mapping(conn_str, row, sf_account_id, existed_before, matched_by, snapshot):
    """Write the SF Account ID into the mapping table. Hard abort if write fails."""
    org_number = normalize_org_number(row.Business_Registration_Number__c)
    try:
        with get_db_connection(conn_str) as conn:
            with conn.cursor() as cursor:
                query = '''
                    INSERT INTO "Mapping"."sitetracker_account_mapping"
                        (mapping_key, grouping_key, org_number_normalized, target_sf_account_id,
                         account_existed_before_migration, matched_by, previous_record_snapshot)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                '''
                params = (
                    row.mapping_key,
                    row.grouping_key,
                    org_number,
                    sf_account_id,
                    existed_before,
                    matched_by,
                    json.dumps(snapshot) if snapshot else None,
                )
                cursor.execute(query, params)
                if cursor.rowcount == 0:
                    raise SystemExit(
                        f"Mapping INSERT affected 0 rows for {row.Name} ({row.grouping_key}); "
                        f"halting to prevent orphaned Account in SiteTracker."
                    )
                conn.commit()
                logging.info(
                    f"    Mapped {row.Name} ({row.grouping_key}) → {sf_account_id} "
                    f"(existed_before={existed_before}, matched_by={matched_by})"
                )
    except SystemExit:
        raise
    except Exception as e:
        raise SystemExit(
            f"Mapping write failed for {row.Name} ({row.grouping_key}) "
            f"(sf_id={sf_account_id}): {e} — halting to prevent orphaned resources."
        )


def build_account_payload(row):
    """Build the Salesforce Account payload from a target view row."""
    payload = {
        "Name": row.Name,
        "Type": row.Type,
        "BillingCountry": row.BillingCountry,
        "ShippingCountry": row.ShippingCountry,
    }

    if row.Business_Registration_Number__c:
        payload["Business_Registration_Number__c"] = row.Business_Registration_Number__c
    if row.BillingStreet:
        payload["BillingStreet"] = row.BillingStreet
    if row.BillingCity:
        payload["BillingCity"] = row.BillingCity
    if row.BillingPostalCode:
        payload["BillingPostalCode"] = row.BillingPostalCode
    if row.ShippingStreet:
        payload["ShippingStreet"] = row.ShippingStreet
    if row.ShippingCity:
        payload["ShippingCity"] = row.ShippingCity
    if row.ShippingPostalCode:
        payload["ShippingPostalCode"] = row.ShippingPostalCode
    if row.Email__c:
        payload["Email__c"] = row.Email__c

    return {k: v for k, v in payload.items() if v is not None}


def process_account(conn_str, row):
    """Process a single account row: create or update in SiteTracker."""
    global created_count, updated_count, error_count

    account_name = row.Name
    grouping_key = row.grouping_key
    sf_id = row.TargetSfAccountId
    org_number = normalize_org_number(row.Business_Registration_Number__c)

    try:
        if sf_id is not None:
            # Already mapped → snapshot + diff + update
            before = snapshot_record(SOBJECT, sf_id)
            payload = build_account_payload(row)
            log_field_diffs(f"{account_name} ({grouping_key})", before, payload)
            sitetracker_update(SOBJECT, sf_id, payload)
            updated_count += 1
            logging.info(f"  UPDATED {account_name} ({grouping_key}) → {sf_id}")
        else:
            # Not mapped — SOQL lookup by org_number, then by name
            existing_record = None
            matched_by = None

            if org_number:
                existing_record = find_account_by_org_number(org_number)
                if existing_record:
                    matched_by = "org_number"

            if not existing_record:
                existing_record = find_account_by_name(account_name)
                if existing_record:
                    matched_by = "name"

            if existing_record:
                # Found in SiteTracker but not in our mapping
                sf_id = existing_record["Id"]
                logging.warning(
                    f"  Account already exists: {account_name} ({grouping_key}) → {sf_id} "
                    f"[matched_by={matched_by}]"
                )

                before = snapshot_record(SOBJECT, sf_id)
                payload = build_account_payload(row)
                log_field_diffs(f"{account_name} ({grouping_key})", before, payload)
                sitetracker_update(SOBJECT, sf_id, payload)

                # Write mapping (hard abort if fails)
                update_account_mapping(conn_str, row, sf_id, True, matched_by, before)
                updated_count += 1
                logging.info(f"  UPDATED (pre-existing) {account_name} → {sf_id}")
                return

            # Create new account
            payload = build_account_payload(row)
            result = sitetracker_create(SOBJECT, payload)
            sf_id = result["id"]

            # Write mapping (hard abort if this fails)
            update_account_mapping(conn_str, row, sf_id, False, "created", None)
            created_count += 1
            logging.info(f"  CREATED {account_name} ({grouping_key}) → {sf_id}")

    except SystemExit:
        raise  # Let mapping failures propagate as hard aborts
    except Exception as e:
        error_count += 1
        errors.append(f"{account_name} ({grouping_key}): {e}")
        logging.error(f"  ERROR processing {account_name} ({grouping_key}): {e}")


def main():
    """Main entry point."""
    global total_rows

    conn_str = get_db_connection_string()

    logging.info("=" * 70)
    logging.info("SiteTracker Accounts — Create or Update")
    logging.info("=" * 70)

    # Authenticate
    get_sitetracker_token()

    # Fetch target data (aborts if view is empty or missing)
    rows = fetch_target_accounts(conn_str)
    total_rows = len(rows)
    logging.info(f"Found {total_rows} accounts to process")

    # Process each account
    for i, row in enumerate(rows, 1):
        if i % 50 == 0:
            logging.info(f"Progress: {i}/{total_rows}")
        process_account(conn_str, row)

    # Summary
    logging.info("")
    logging.info("=" * 70)
    logging.info(f"COMPLETE: {total_rows} accounts processed")
    logging.info(f"  Created: {created_count}")
    logging.info(f"  Updated: {updated_count}")
    logging.info(f"  Errors:  {error_count}")
    if errors:
        logging.info("  Error details:")
        for err in errors:
            logging.error(f"    {err}")
    logging.info("=" * 70)

    if error_count > 0:
        sys.exit(1)


if __name__ == "__main__":
    setup_logging("CreateOrUpdateSiteTrackerAccounts")
    try:
        main()
    except SystemExit as e:
        logging.critical(f"ABORT: {e}")
        sys.exit(1)
    except Exception as e:
        logging.error(f"An error occurred: {e}")
        sys.exit(1)
