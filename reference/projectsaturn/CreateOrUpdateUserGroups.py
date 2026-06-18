import pyodbc
import requests
import json
import logging
from collections import defaultdict
from utils.config_utils import get_db_connection_string, get_api_base_url, get_api_token
from utils.log_utils import setup_logging
from utils.ampeco_utils import execute_ampeco_api_call, check_ampeco_response
from utils.dbutils import get_sql_dialect, quote_identifier, get_db_connection

# Constants
conn_str = get_db_connection_string()

# Initialize counters and error list
total_rows = 0
created_count = 0
updated_count = 0
error_count = 0
errors = []
user_update_count = 0

def get_value(value, expected_type, default=None, decimals=None):
    """Utility function to get value or default with type checking and rounding."""
    ## TODO: Remove this location function and use the one in dbutils instead!
    if value is None:
        return default
    if expected_type == bool:
        return bool(value)
    if expected_type == int:
        return int(value)
    if expected_type == float:
        value = float(value)
        if decimals is not None:
            value = round(value, decimals)
        return value
    if expected_type == str:
        return str(value)
    return value

def fetch_user_groups(conn_str: str):
    """Fetch all User Groups (dialect-aware). Use SELECT * and filter by TargetPartnerID IS NOT NULL."""
    with get_db_connection(conn_str) as conn:
        cursor = conn.cursor()
        dialect = get_sql_dialect(conn)
        target_schema = quote_identifier("Target", dialect)
        table = quote_identifier("UserGroups", dialect)
        target_partner_id = quote_identifier("TargetPartnerID", dialect)
        query = f"SELECT * FROM {target_schema}.{table} WHERE {target_partner_id} IS NOT NULL"
        cursor.execute(query)
        return cursor.fetchall()

def fetch_user_group_members(conn_str: str):
    """Fetch all User Group Members (dialect-aware). Use SELECT * and filter by TargetUserGroupID IS NOT NULL AND TargetUserID IS NOT NULL."""
    with get_db_connection(conn_str) as conn:
        cursor = conn.cursor()
        dialect = get_sql_dialect(conn)
        target_schema = quote_identifier("Target", dialect)
        table = quote_identifier("UserGroupMembers", dialect)
        tgt_group_id = quote_identifier("TargetUserGroupID", dialect)
        tgt_user_id = quote_identifier("TargetUserID", dialect)
        query = f"SELECT * FROM {target_schema}.{table} WHERE {tgt_group_id} IS NOT NULL AND {tgt_user_id} IS NOT NULL"
        cursor.execute(query)
        return cursor.fetchall()

def create_user_group(user_group_data):
    """Create a user group in Ampeco API."""
    def api_call(base_url, headers):
        url = f"{base_url}/resources/user-groups/v1.0"
        return requests.post(url, headers=headers, json=user_group_data)

    response = execute_ampeco_api_call(api_call)
    return check_ampeco_response(response, 201, 'create_user_group')

def update_user_group(user_group_id, user_group_data):
    """Update a user group in Ampeco API."""
    def api_call(base_url, headers):
        url = f"{base_url}/resources/user-groups/v1.0/{user_group_id}"
        return requests.put(url, headers=headers, json=user_group_data)

    response = execute_ampeco_api_call(api_call)
    return check_ampeco_response(response, 200, 'update_user_group')

def update_group_mapping(conn_str: str, row, user_group_id):
    """Update mapping using a single-query pattern.

    - If row has SourcePriceGroupID (Charge365/PostgreSQL), UPDATE "Mapping"."PriceGroupMapping" by SourcePriceGroupID.
    - Else if row has SourceGroupID (MSSQL), INSERT into [Mapping].[GroupMapping].
    """
    with get_db_connection(conn_str) as conn:
        cursor = conn.cursor()

        query = None
        params = None
        success_msg = None

        # Generic mapping_table/mapping_key pattern (key-based migrations like Project Sleet)
        if hasattr(row, 'mapping_table') and row.mapping_table:
            table_name = row.mapping_table  # e.g., "user_group_mapping"
            query = f'''
                INSERT INTO "Mapping"."{table_name}" (mapping_key, target_user_group_id)
                VALUES (?, ?)
            '''
            params = (row.mapping_key, user_group_id)
            success_msg = (
                f"Inserted {table_name} for mapping_key: {row.mapping_key} -> target_user_group_id: {user_group_id}"
            )

        # PostgreSQL path: upsert PriceGroupMapping using SourcePriceGroupID key
        elif hasattr(row, 'SourcePriceGroupID') and getattr(row, 'SourcePriceGroupID') is not None:
            # Updated to include TargetTariffType with hardcoded value 'Usergroup' matching new composite key
            query = (
                """
                INSERT INTO "Mapping"."PriceGroupMapping" ("SourcePriceGroupID", "TargetTariffType", "TargetUserGroupID")
                VALUES (?, ?, ?)
                ON CONFLICT ("SourcePriceGroupID","TargetTariffType") DO UPDATE
                SET "TargetUserGroupID" = EXCLUDED."TargetUserGroupID"
                """
            )
            params = (getattr(row, 'SourcePriceGroupID'), 'Usergroup', user_group_id)
            success_msg = (
                f"Upserted PriceGroupMapping (Usergroup) for SourcePriceGroupID: {getattr(row, 'SourcePriceGroupID')} -> TargetUserGroupID: {user_group_id}"
            )

        # MSSQL/default path: insert GroupMapping using SourceGroupID
        elif hasattr(row, 'SourceGroupID'):
            query = (
                """
                INSERT INTO [Mapping].[GroupMapping] (SourceGroupID, TargetUserGroupID)
                VALUES (?, ?)
                """
            )
            params = (getattr(row, 'SourceGroupID'), user_group_id)
            success_msg = f"Inserted GroupMapping for SourceGroupID: {getattr(row, 'SourceGroupID')} -> TargetUserGroupID: {user_group_id}"

        if query is None or params is None:
            raise SystemExit("No valid mapping condition found; query not built. Halting execution.")

        cursor.execute(query, params)
        if cursor.rowcount == 0:
            raise SystemExit("Mapping change affected 0 rows; halting execution to prevent missing mapping.")
        conn.commit()
        logging.info(success_msg)

def fetch_user_by_id(user_id):
    """Fetch user by ID from Ampeco API."""
    def api_call(base_url, headers):
        url = f"{base_url}/resources/users/v1.0/{user_id}"
        return requests.get(url, headers=headers)

    response = execute_ampeco_api_call(api_call)
    return check_ampeco_response(response, 200, 'fetch_user_by_id')

def update_user_groups(user_id, user_group_ids):
    """Update user groups for a user in Ampeco API."""
    def api_call(base_url, headers):
        url = f"{base_url}/resources/users/v1.0/{user_id}"
        return requests.patch(url, headers=headers, json={"userGroupIds": user_group_ids})

    response = execute_ampeco_api_call(api_call)
    return check_ampeco_response(response, 200, 'update_user_groups')

def truncate_description(description, max_length=250):
    """Truncate the description to a maximum length and add '...' if truncated."""
    if description and len(description) > max_length:
        return description[:max_length] + "..."
    return description

def process_user_group(conn_str: str, row):
    """Processes each user group row."""
    global created_count, updated_count, error_count
    target_user_group_id = row.TargetUserGroupID

    user_group_data = {
        "name": get_value(row.name, str),
        "partnerId": get_value(row.partnerId, int),
        "description": truncate_description(get_value(row.description, str))
    }

    try:
        if target_user_group_id is None:
            # Create a new User Group
            response_data = create_user_group(user_group_data)
            target_user_group_id = response_data['id']

            # Update the mapping table
            update_group_mapping(conn_str, row, target_user_group_id)
            created_count += 1
            logging.info(f"Created user group '{row.name}' with id={target_user_group_id}")
        else:
            # Update the existing User Group
            update_user_group(target_user_group_id, user_group_data)
            updated_count += 1
            logging.info(f"Updated user group '{row.name}' (id={target_user_group_id})")
    except Exception as e:
        error_count += 1
        errors.append((row, str(e)))
        logging.error(f"Error processing user group '{row.name}': {e}")

def process_user_group_members(conn_str: str):
    """Process user group memberships."""
    global user_update_count, error_count  # Add error_count here
    user_group_members = fetch_user_group_members(conn_str)
    user_groups_by_user = defaultdict(list)

    for member in user_group_members:
        user_groups_by_user[member.TargetUserID].append(member.TargetUserGroupID)

    total_users = len(user_groups_by_user)
    logging.info(f"Total users to update: {total_users}")

    for index, (user_id, group_ids) in enumerate(user_groups_by_user.items(), start=1):
        logging.info(f"Processing User {user_id} ({index} of {total_users})...")
        try:
            user = fetch_user_by_id(user_id)
            existing_group_ids = user.get('userGroupIds', [])
            updated_group_ids = list(set(existing_group_ids + group_ids))
            update_user_groups(user_id, updated_group_ids)
            user_update_count += 1
        except Exception as e:
            error_count += 1
            errors.append((user_id, str(e)))
            logging.error(f"Error processing user {user_id}: {e}")

def main():
    """Main function to fetch and process user groups."""
    global total_rows
    if not conn_str:
        raise SystemExit("Database connection string is not configured.")
    user_groups = fetch_user_groups(conn_str)
    total_rows = len(user_groups)
    logging.info(f"Total user groups fetched: {total_rows}")

    for index, row in enumerate(user_groups, start=1):
        display_name = getattr(row, 'name', '<unnamed>')
        logging.info(f"Processing UserGroup:'{display_name}' ({index} of {total_rows})...")
        process_user_group(conn_str, row)

    # Process user group memberships
    process_user_group_members(conn_str)

    # Summarize results
    logging.info(f"Total rows processed: {total_rows}")
    logging.info(f"User Groups created: {created_count}")
    logging.info(f"User Groups updated: {updated_count}")
    logging.info(f"Users updated: {user_update_count}")
    logging.info(f"Errors encountered: {error_count}")

    if errors:
        logging.info("Rows causing errors and their exceptions:")
        for error_row, error_msg in errors:
            logging.info(f"Row: {error_row}, Error: {error_msg}")

if __name__ == "__main__":
    setup_logging("CreateOrUpdateUserGroups")
    try:
        main()
    except Exception as e:
        logging.error(f"An error occurred: {e}")
