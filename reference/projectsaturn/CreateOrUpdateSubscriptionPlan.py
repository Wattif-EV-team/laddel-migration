import pyodbc
import requests
import json
from typing import cast
import logging
from utils.config_utils import get_db_connection_string, get_api_base_url, get_api_token
from utils.log_utils import setup_logging
from utils.ampeco_utils import execute_ampeco_api_call, check_ampeco_response
from utils.dbutils import get_value, get_db_connection, get_sql_dialect, quote_identifier

# Constants
conn_str = get_db_connection_string()

# Initialize counters and error list
total_rows = 0
created_count = 0
updated_count = 0
error_count = 0
errors = []

def fetch_subscription_plans():
    """Fetch all Subscription Plans from the database (dialect-aware), only rows with TargetPartnerID."""
    local_conn_str = conn_str
    if not local_conn_str:
        raise SystemExit("Database connection string is not configured.")
    with get_db_connection(local_conn_str) as conn:
        cursor = conn.cursor()
        dialect = get_sql_dialect(conn)
        target_schema = quote_identifier("Target", dialect)
        table = quote_identifier("SubscriptionPlan", dialect)
        target_partner_id = quote_identifier("TargetPartnerID", dialect)
        query = f"SELECT * FROM {target_schema}.{table} WHERE {target_partner_id} IS NOT NULL"
        cursor.execute(query)
        return cursor.fetchall()

def create_subscription_plan(subscription_plan_data):
    """Create a Subscription Plan in Ampeco API."""
    def api_call(base_url, headers):
        url = f"{base_url}/resources/subscription-plans/v2.0"
        return requests.post(url, headers=headers, json=subscription_plan_data)

    response = execute_ampeco_api_call(api_call)
    return check_ampeco_response(response, 201, 'create_subscription_plan')

def update_subscription_plan(subscription_plan_id, subscription_plan_data):
    """Update a Subscription Plan in Ampeco API."""
    def api_call(base_url, headers):
        url = f"{base_url}/resources/subscription-plans/v2.0/{subscription_plan_id}"
        return requests.patch(url, headers=headers, json=subscription_plan_data)

    response = execute_ampeco_api_call(api_call)
    return check_ampeco_response(response, 200, 'update_subscription_plan')

def has_subscribers(plan_id):
    """Check if a Subscription Plan has existing subscribers."""
    def api_call(base_url, headers):
        url = f"{base_url}/resources/subscriptions/v1.0?filter[planId]={plan_id}"
        return requests.get(url, headers=headers)

    response = execute_ampeco_api_call(api_call)
    data = check_ampeco_response(response, 200, 'has_subscribers')
    return len(data) > 0

def read_subscription_plan(plan_id):
    """Fetch the current Subscription Plan details."""
    def api_call(base_url, headers):
        url = f"{base_url}/resources/subscription-plans/v2.0/{plan_id}"
        return requests.get(url, headers=headers)

    response = execute_ampeco_api_call(api_call)
    return check_ampeco_response(response, 200, 'read_subscription_plan')

def update_subscription_plan_mapping(row, subscription_plan_id):
    """Update ProjectCodeMapping with the new SubscriptionPlanID using ProjectCode as key.

    Single-query pattern: choose one dialect-specific UPDATE and execute.
    - If SourceAccountID is present on the row, perform a PostgreSQL-style UPDATE into Mapping.ProjectCodeMapping.
    - Else, default to SQL Server UPDATE into [Mapping].[ProjectCodeMapping].
    """
    project_code = row.ProjectCode
    local_conn_str = conn_str
    if not local_conn_str:
        raise SystemExit("Database connection string is not configured.")
    with get_db_connection(local_conn_str) as conn:
        cursor = conn.cursor()

        query = None
        params = None
        success_msg = None

        # PostgreSQL branch when SourceAccountID is present on the row
        if hasattr(row, "SourceAccountID"):
            query = (
                """
                UPDATE "Mapping"."ProjectCodeMapping"
                SET "TargetSubscriptionPlanID" = ?
                WHERE "ProjectCode" = ?
                """
            )
            params = (subscription_plan_id, project_code)
            success_msg = (
                f"Updated TargetSubscriptionPlanID for ProjectCode: {project_code} with SubscriptionPlanID: {subscription_plan_id} (PostgreSQL)"
            )
        # Default: SQL Server UPDATE
        else:
            query = (
                """
                UPDATE [Mapping].[ProjectCodeMapping]
                SET TargetSubscriptionPlanID = ?
                WHERE ProjectCode = ?
                """
            )
            params = (subscription_plan_id, project_code)
            success_msg = (
                f"Updated TargetSubscriptionPlanID for ProjectCode: {project_code} with SubscriptionPlanID: {subscription_plan_id} (SQL Server)"
            )

        if query is None or params is None:
            raise SystemExit("No mapping update query built; halting execution.")

        try:
            cursor.execute(query, params)
            if cursor.rowcount == 0:
                raise SystemExit(f"No rows updated for ProjectCode: {project_code}")
            conn.commit()
            logging.info(success_msg)
        except Exception as e:
            logging.error(
                f"Failed to update ProjectCodeMapping for ProjectCode: {project_code}. Error: {e}"
            )
            raise SystemExit(f"Halting execution due to error: {e}")

def process_subscription_plan(row):
    """Processes each subscription plan row."""
    global created_count, updated_count, error_count
    subscription_plan_id = row.TargetSubscriptionPlanID
    subscription_plan_data = {
        "name": [],
        "description": [],
        "renewalCycle": get_value(row.renewalCycle, str),
        "type": get_value(row.type, str),
        "status": get_value(row.status, str, "enabled"),
        "postPaidChargingSessionsAccumulation": get_value(row.postPaidChargingSessionsAccumulation, str),
        "visibilityRestrictions": {
            "includedPartnerUsers": json.loads(cast(str, get_value(row.visibilityRestrictions_includedPartnerUsers, str, "[]"))),
            "excludedPartnerUsers": [],
            "includedUserGroups": [],
            "excludedUserGroups": []
        },
        "billingUsageThreshold": get_value(row.billingUsageThreshold, int),
        "baseFee": get_value(row.baseFee, float),
        "baseFeeAppliesPerEachHomeCharger": get_value(row.baseFeeAppliesPerEachHomeCharger, bool, False),
        "freeRenewalPeriods": get_value(row.freeRenewalPeriods, int),
    }

    # Dynamically build translation objects for name and description
    for column_name in row.cursor_description:
        col = column_name[0]
        if col.startswith("name_"):
            locale = col.split("_", 1)[1]
            subscription_plan_data["name"].append({"locale": locale, "translation": get_value(getattr(row, col), str)})
        elif col.startswith("description_"):
            locale = col.split("_", 1)[1]
            subscription_plan_data["description"].append({"locale": locale, "translation": get_value(getattr(row, col), str)})

    try:
        if subscription_plan_id is None:
            # Create a new Subscription Plan
            response_data = create_subscription_plan(subscription_plan_data)
            subscription_plan_id = response_data['id']
            created_count += 1
            logging.info(f"Created subscription plan '{get_value(row.name_en, str)}' with id={subscription_plan_id}")
            # Update mapping table with new SubscriptionPlanID
            update_subscription_plan_mapping(row, subscription_plan_id)
        else:
            # Check if the subscription plan has subscribers
            if has_subscribers(subscription_plan_id):
                current_plan = read_subscription_plan(subscription_plan_id)
                read_only_fields = ['type', 'baseFee', 'renewalCycle']
                differences = []

                for field in read_only_fields:
                    if subscription_plan_data.get(field) != current_plan.get(field):
                        differences.append(f"{field}: current={current_plan.get(field)}, new={subscription_plan_data.get(field)}")

                if differences:
                    logging.warning(f"Read-only field differences for SubscriptionPlanID {subscription_plan_id}: {differences}")
                    logging.warning("A new subscription plan must be created to change these values.")
                    # Create a new Subscription Plan
                    response_data = create_subscription_plan(subscription_plan_data)
                    subscription_plan_id = response_data['id']
                    created_count += 1
                    logging.info(f"Created new subscription plan '{get_value(row.name_en, str)}' with id={subscription_plan_id}")
                    # Update mapping table with new SubscriptionPlanID
                    update_subscription_plan_mapping(row, subscription_plan_id)
                    return

                # Remove read-only fields from subscription_plan_data
                for field in read_only_fields:
                    subscription_plan_data.pop(field, None)

            # Update the existing Subscription Plan
            update_subscription_plan(subscription_plan_id, subscription_plan_data)
            updated_count += 1
            logging.info(f"Updated subscription plan '{get_value(row.name_en, str)}' (id={subscription_plan_id})")
    except Exception as e:
        error_count += 1
        errors.append((row, str(e)))
        logging.error(f"Error processing row {row}: {e}")

def main():
    """Main function to fetch and process subscription plans."""
    global total_rows
    subscription_plans = fetch_subscription_plans()
    total_rows = len(subscription_plans)
    logging.info(f"Total subscription plans fetched: {total_rows}")

    for index, row in enumerate(subscription_plans, start=1):
        logging.info(f"Processing Subscription Plan {index} of {total_rows} (ProjectCode: {row.ProjectCode})")
        process_subscription_plan(row)

    # Summarize results
    logging.info(f"Total rows processed: {total_rows}")
    logging.info(f"Subscription Plans created: {created_count}")
    logging.info(f"Subscription Plans updated: {updated_count}")
    logging.info(f"Errors encountered: {error_count}")

    if errors:
        logging.info("Rows causing errors and their exceptions:")
        for error_row, error_msg in errors:
            logging.info(f"Row: {error_row}, Error: {error_msg}")

if __name__ == "__main__":
    setup_logging("CreateOrUpdateSubscriptionPlan")
    try:
        main()
    except Exception as e:
        logging.error(f"An error occurred: {e}")
