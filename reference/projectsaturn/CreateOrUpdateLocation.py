import pyodbc
import requests
import json
import logging
from typing import cast
from utils.config_utils import get_db_connection_string, get_api_base_url, get_api_token
from utils.log_utils import setup_logging
from utils.ampeco_utils import execute_ampeco_api_call, check_ampeco_response
from utils.dbutils import get_value, get_db_connection, get_sql_dialect, quote_identifier
from utils.locale_utils import normalize_locale

# Constants
conn_str = get_db_connection_string()

# Initialize counters and error list
total_rows = 0
created_count = 0
updated_count = 0
error_count = 0
errors = []

def fetch_locations(conn_str):
    """Fetch all locations from the database."""
    with get_db_connection(conn_str) as conn:
        with conn.cursor() as cursor:
            dialect = get_sql_dialect(conn)
            target_schema = quote_identifier("Target", dialect)
            table = quote_identifier("Locations", dialect)
            query = f"SELECT * FROM {target_schema}.{table}"
            cursor.execute(query)
            return cursor.fetchall()

def create_location(location_data):
    """Create a location in Ampeco API."""
    def api_call(base_url, headers):
        url = f"{base_url}/resources/locations/v2.0"
        return requests.post(url, headers=headers, json=location_data)

    response = execute_ampeco_api_call(api_call)
    return check_ampeco_response(response, 201, 'create_location')

def update_location(location_id, location_data):
    """Update a location in Ampeco API."""
    def api_call(base_url, headers):
        url = f"{base_url}/resources/locations/v2.0/{location_id}"
        return requests.patch(url, headers=headers, json=location_data)

    response = execute_ampeco_api_call(api_call)
    return check_ampeco_response(response, 200, 'update_location')

def update_location_mapping(conn_str, row, location_id):
    """Update the appropriate mapping table with the new LocationID based on available source identifiers."""
    try:
        with get_db_connection(conn_str) as conn:
            with conn.cursor() as cursor:
                query = None
                params = None
                success_msg = None

                # Generic mapping_table/mapping_key pattern (key-based migrations with merge support)
                if hasattr(row, "mapping_table") and row.mapping_table:
                    table_name = row.mapping_table  # e.g., "location_mapping"
                    # Location uses merge: update all rows where mapping_key matches OR merge_with_mapping_key matches
                    query = f'''
                        UPDATE "Mapping"."{table_name}"
                        SET target_location_id = ?
                        WHERE mapping_key = ? OR merge_with_mapping_key = ?
                    '''
                    params = (location_id, row.mapping_key, row.mapping_key)
                    success_msg = f"Updated {table_name} for mapping_key: {row.mapping_key} with target_location_id {location_id}"
                # StationMapping (MSSQL Current) when SourceStationID is present
                elif hasattr(row, "SourceStationID"):
                    query = (
                        """
                        UPDATE [Mapping].[StationMapping]
                        SET TargetLocationID = ?
                        WHERE SourceStationID = ? OR MergeLocationWithSourceStationID = ?
                        """
                    )
                    params = (location_id, row.SourceStationID, row.SourceStationID)
                    success_msg = f"Updated StationMapping with location: {row.SourceStationID}, {location_id}"
                # LocationMapping (Charge365 - PostgreSQL) when SourceLocationID and SourceAccountID are present
                elif hasattr(row, "SourceLocationID") and hasattr(row, "SourceAccountID"):
                    query = (
                        """
                        UPDATE "Mapping"."LocationMapping"
                        SET "TargetLocationID" = ?
                        WHERE "SourceLocationID" = ? OR "MergeLocationWithSourceLocationID" = ?
                        """
                    )
                    params = (location_id, row.SourceLocationID, row.SourceLocationID)
                    success_msg = f"Updated LocationMapping (PG) with location: {row.SourceLocationID}, {location_id}"
                # LocationMapping (MSSQL) when only SourceLocationID is present
                elif hasattr(row, "SourceLocationID"):
                    query = (
                        """
                        UPDATE [Mapping].[LocationMapping]
                        SET TargetLocationID = ?
                        WHERE SourceLocationId = ? OR MergeLocationWithSourceLocationId = ?
                        """
                    )
                    params = (location_id, row.SourceLocationID, row.SourceLocationID)
                    success_msg = f"Updated LocationMapping with location: {row.SourceLocationID}, {location_id}"

                if query is None or params is None:
                    raise SystemExit("No valid mapping condition found; query not built. Halting execution.")

                cursor.execute(query, params)
                if cursor.rowcount == 0:
                    raise SystemExit("Update affected 0 rows; halting execution to prevent missing mapping.")
                logging.info(success_msg)
                conn.commit()
    except Exception as e:
        logging.error(f"Failed to update mapping tables for {row} with TargetLocationID: {location_id}. Error: {e}")
        raise SystemExit(f"Halting execution due to error: {e}")

def process_location(row):
    """Processes each location row."""
    global created_count, updated_count, error_count
    location_id = row.TargetLocationID
    location_data = {
        "status": get_value(row.status, str, "enabled"),
        "geoposition": {
            "latitude": get_value(row.geoposition_latitude, float),
            "longitude": get_value(row.geoposition_longitude, float)
        },
        "country": get_value(row.country, str, "SE"),
        "name": [],
        "shortDescription": [],
        "description": [],
        "additionalDescription": [],
        "address": [],
        "streetAddress": [],
        "city": get_value(row.city, str),
        "region": get_value(row.region, str),
        "postCode": get_value(row.postCode, str),
        "externalId": get_value(row.externalId, str),
    "tags": json.loads(cast(str, get_value(row.tags, str, "[]")))
    }

    # Dynamically build translation objects for name, shortDescription, description, additionalDescription, and address
    for column_name in row.cursor_description:
        col = column_name[0]
        if col.startswith("name_"):
            locale = normalize_locale(col.split("_", 1)[1])
            translation = get_value(getattr(row, col), str)
            if translation:  # Only append if translation is not empty
                location_data["name"].append({"locale": locale, "translation": translation})
        elif col.startswith("shortDescription_"):
            locale = normalize_locale(col.split("_", 1)[1])
            translation = get_value(getattr(row, col), str)
            if translation:  # Only append if translation is not empty
                location_data["shortDescription"].append({"locale": locale, "translation": translation})
        elif col.startswith("description_"):
            locale = normalize_locale(col.split("_", 1)[1])
            translation = get_value(getattr(row, col), str, default="<div></div>")
            if translation and translation != "<div></div>":  # Only append if translation is not empty and not the default
                location_data["description"].append({"locale": locale, "translation": translation})
        elif col.startswith("additionalDescription_"):
            locale = normalize_locale(col.split("_", 1)[1])
            translation = get_value(getattr(row, col), str)
            if translation:  # Only append if translation is not empty
                location_data["additionalDescription"].append({"locale": locale, "translation": translation})
        elif col.startswith("address_"):
            locale = normalize_locale(col.split("_", 1)[1])
            translation = get_value(getattr(row, col), str)
            if translation:  # Only append if translation is not empty
                location_data["address"].append({"locale": locale, "translation": translation})
        elif col.startswith("streetAddress_"):
            locale = normalize_locale(col.split("_", 1)[1])
            translation = get_value(getattr(row, col), str)
            if translation:  # Only append if translation is not empty
                location_data["streetAddress"].append({"locale": locale, "translation": translation})                

    try:
        if location_id is None:
            # Create a new Location
            response_data = create_location(location_data)
            location_id = response_data['id']
            created_count += 1
            logging.info(f"Created location '{get_value(row.name_en, str)}' with id={location_id}")
            # Update mapping table with new LocationID
            update_location_mapping(conn_str, row, location_id)
        else:
            # Update the existing Location
            update_location(location_id, location_data)
            updated_count += 1
            logging.info(f"Updated location '{get_value(row.name_en, str)}' (id={location_id})")
    except Exception as e:
        error_count += 1
        errors.append((row, str(e)))
        logging.error(f"Error processing row {row}: {e}")

def main():
    """Main function to fetch and process locations."""
    global total_rows
    locations = fetch_locations(conn_str)
    total_rows = len(locations)
    logging.info(f"Total locations fetched: {total_rows}")

    for index, row in enumerate(locations, start=1):
        logging.info(f"Processing Location {index} of {total_rows}...")
        process_location(row)

    # Summarize results
    logging.info(f"Total rows processed: {total_rows}")
    logging.info(f"Locations created: {created_count}")
    logging.info(f"Locations updated: {updated_count}")
    logging.info(f"Errors encountered: {error_count}")

    if errors:
        logging.info("Rows causing errors and their exceptions:")
        for error_row, error_msg in errors:
            logging.info(f"Row: {error_row}, Error: {error_msg}")

if __name__ == "__main__":
    setup_logging("CreateOrUpdateLocation")
    try:
        main()
    except Exception as e:
        logging.error(f"An error occurred: {e}")
