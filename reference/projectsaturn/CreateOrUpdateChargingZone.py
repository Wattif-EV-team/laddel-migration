import pyodbc
import requests
import json
import logging
from utils.config_utils import get_db_connection_string, get_api_base_url, get_api_token
from utils.log_utils import setup_logging
from utils.ampeco_utils import execute_ampeco_api_call, check_ampeco_response
from utils.dbutils import get_value, get_db_connection, get_sql_dialect, quote_identifier

# Initialize counters and error list
total_rows = 0
created_count = 0
updated_count = 0
error_count = 0
errors = []

def fetch_charging_zones(conn_str):
    """Fetch all charging zones from the database."""
    with get_db_connection(conn_str) as conn:
        with conn.cursor() as cursor:
            dialect = get_sql_dialect(conn)
            target_schema = quote_identifier("Target", dialect)
            table = quote_identifier("ChargingZones", dialect)
            target_location_id = quote_identifier("TargetLocationID", dialect)
            query = f"SELECT * FROM {target_schema}.{table} WHERE {target_location_id} IS NOT NULL"
            cursor.execute(query)
            return cursor.fetchall()

def create_charging_zone(location_id, charging_zone_data):
    """Create a charging zone in Ampeco API."""
    def api_call(base_url, headers):
        url = f"{base_url}/resources/locations/v2.0/{location_id}/charging-zones"
        return requests.post(url, headers=headers, json=charging_zone_data)

    response = execute_ampeco_api_call(api_call)
    return check_ampeco_response(response, 201, 'create_charging_zone')

def update_charging_zone(location_id, charge_zone_id, charging_zone_data):
    """Update a charging zone in Ampeco API."""
    def api_call(base_url, headers):
        url = f"{base_url}/resources/locations/v2.0/{location_id}/charging-zones/{charge_zone_id}"
        return requests.patch(url, headers=headers, json=charging_zone_data)

    response = execute_ampeco_api_call(api_call)
    return check_ampeco_response(response, 200, 'update_charging_zone')

def update_charging_zone_mapping(conn_str, row, charge_zone_id):
    """
    Update the appropriate mapping table with the new ChargeZoneID.
    Supports both StationMapping and LocationMapping based on available source identifiers.
    """
    try:
        with get_db_connection(conn_str) as conn:
            with conn.cursor() as cursor:
                query = None
                params = None
                success_msg = None

                # Generic mapping_table/mapping_key pattern (key-based migrations, no merge for zones)
                if hasattr(row, "mapping_table") and row.mapping_table:
                    table_name = row.mapping_table  # e.g., "location_mapping"
                    query = f'''
                        UPDATE "Mapping"."{table_name}"
                        SET target_charge_zone_id = ?
                        WHERE mapping_key = ?
                    '''
                    params = (charge_zone_id, row.mapping_key)
                    success_msg = f"Updated {table_name} for mapping_key: {row.mapping_key} with target_charge_zone_id {charge_zone_id}"
                # Choose exactly one update based on available attributes
                # StationMapping (MSSQL) when SourceStationID is present
                elif hasattr(row, "SourceStationID"):
                    query = (
                        """
                        UPDATE [Mapping].[StationMapping]
                        SET TargetChargeZoneID = ?
                        WHERE SourceStationID = ?
                        """
                    )
                    params = (charge_zone_id, row.SourceStationID)
                    success_msg = (
                        f"Updated StationMapping with location: {row.SourceStationID} for TargetChargeZoneID {charge_zone_id}"
                    )
                # LocationMapping (Charge365 - PostgreSQL) when SourceLocationID and SourceAccountID are present
                elif hasattr(row, "SourceLocationID") and hasattr(row, "SourceAccountID"):
                    query = (
                        """
                        UPDATE "Mapping"."LocationMapping"
                        SET "TargetChargeZoneID" = ?
                        WHERE "SourceLocationID" = ?
                        """
                    )
                    params = (charge_zone_id, row.SourceLocationID)
                    success_msg = (
                        f"Updated LocationMapping (PG) with location: {row.SourceLocationID} for TargetChargeZoneID {charge_zone_id}"
                    )
                # LocationMapping (MSSQL) when only SourceLocationID is present
                elif hasattr(row, "SourceLocationID"):
                    query = (
                        """
                        UPDATE [Mapping].[LocationMapping]
                        SET TargetChargeZoneID = ?
                        WHERE SourceLocationID = ?
                        """
                    )
                    params = (charge_zone_id, row.SourceLocationID)
                    success_msg = (
                        f"Updated LocationMapping with location: {row.SourceLocationID} for TargetChargeZoneID {charge_zone_id}"
                    )

                if query is None or params is None:
                    raise SystemExit("No valid mapping condition found; query not built. Halting execution.")

                cursor.execute(query, params)
                if cursor.rowcount == 0:
                    raise SystemExit("Update affected 0 rows; halting execution to prevent missing mapping.")
                logging.info(success_msg)
                conn.commit()
    except Exception as e:
        logging.error(f"Failed to update mapping tables for row ({row}) with ChargeZoneID: {charge_zone_id} for row ({row})]. Error: {e}")
        raise SystemExit(f"Halting execution due to error: {e}")

def process_charging_zone(conn_str, row):
    """Processes each charging zone row."""
    global created_count, updated_count, error_count
    charge_zone_id = row.TargetChargeZoneID
    location_id = row.TargetLocationID
    charging_zone_data = {
        "status": get_value(row.status, str, "enabled"),
        "name": get_value(row.name, str),
        "additionalInfo": {
            "enabled": get_value(row.additionalInfo_enabled, bool, True),
            "title": []
        }
    }

    # Dynamically build translation objects for additionalInfo.title
    for column_name in row.cursor_description:
        col = column_name[0]
        if col.startswith("additionalInfo_title_"):
            locale = col.split("_", 2)[2]
            locale_value = "sv-SE" if locale == "sv" else locale
            translation_value = get_value(getattr(row, col), str)
            charging_zone_data["additionalInfo"]["title"].append({
                "locale": locale_value, 
                "translation": translation_value
            })

    try:
        if charge_zone_id is None:
            # Create a new Charging Zone
            response_data = create_charging_zone(location_id, charging_zone_data)
            charge_zone_id = response_data['id']
            created_count += 1
            logging.info(f"Created Charging Zone ID for: {charge_zone_id}")
            # Update mapping table with new ChargeZoneID
            update_charging_zone_mapping(conn_str, row, charge_zone_id)
        else:
            # Update the existing Charging Zone
            update_charging_zone(location_id, charge_zone_id, charging_zone_data)
            updated_count += 1
            logging.info(f"Updated Charging Zone ID: {charge_zone_id}")
    except Exception as e:
        error_count += 1
        errors.append((row, str(e)))
        logging.error(f"Error processing row ({row}): {e}")

def main():
    """Main function to fetch and process charging zones."""
    global total_rows
    conn_str = get_db_connection_string()
    charging_zones = fetch_charging_zones(conn_str)
    total_rows = len(charging_zones)
    logging.info(f"Total charging zones fetched: {total_rows}")

    for index, row in enumerate(charging_zones):
        logging.info(f"Processing {index + 1} of {total_rows}..")
        process_charging_zone(conn_str, row)

    # Summarize results
    logging.info(f"Total rows processed: {total_rows}")
    logging.info(f"Charging Zones created: {created_count}")
    logging.info(f"Charging Zones updated: {updated_count}")
    logging.info(f"Errors encountered: {error_count}")

    if errors:
        logging.info("Rows causing errors and their exceptions:")
        for error_row, error_msg in errors:
            logging.info(f"Row: {error_row}, Error: {error_msg}")

if __name__ == "__main__":
    setup_logging("CreateOrUpdateChargingZone")
    try:
        main()
    except Exception as e:
        logging.error(f"An error occurred: {e}")
