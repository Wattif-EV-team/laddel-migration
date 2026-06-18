import requests
import logging
import pyodbc
from utils.config_utils import get_db_connection_string
from utils.log_utils import setup_logging
from utils.ampeco_utils import execute_ampeco_api_call, check_ampeco_response
from utils.dbutils import get_db_connection, get_sql_dialect, quote_identifier, get_value

# Initialize counters and error list
stats = {
    'total': 0,
    'created': 0,
    'updated': 0,
    'errors': [],
    'warnings': []
}


def fetch_electricity_meters(conn_str):
    """Fetch electricity meters from Target.ElectricityMeters.

    Returns an empty list and logs a warning if the view does not exist
    (PostgreSQL SQLSTATE 42P01), allowing the caller to skip gracefully.
    """
    try:
        with get_db_connection(conn_str) as conn:
            cursor = conn.cursor()
            dialect = get_sql_dialect(conn)
            target_schema = quote_identifier("Target", dialect)
            table = quote_identifier("ElectricityMeters", dialect)
            query = f"SELECT * FROM {target_schema}.{table} ORDER BY mapping_key"
            cursor.execute(query)
            return cursor.fetchall()
    except pyodbc.ProgrammingError as e:
        if e.args and e.args[0] == "42P01":
            logging.warning("View Target.ElectricityMeters does not exist — skipping electricity meter step.")
            return []
        raise


def create_electricity_meter(meter_data):
    """Create an electricity meter in Ampeco API."""
    def api_call(base_url, headers):
        url = f"{base_url}/resources/electricity-meters/v1.0"
        return requests.post(url, headers=headers, json=meter_data)

    response = execute_ampeco_api_call(api_call)
    return check_ampeco_response(response, 201, 'create_electricity_meter')


def update_electricity_meter(meter_id, meter_data):
    """Update an electricity meter in Ampeco API."""
    def api_call(base_url, headers):
        url = f"{base_url}/resources/electricity-meters/v1.0/{meter_id}"
        return requests.patch(url, headers=headers, json=meter_data)

    response = execute_ampeco_api_call(api_call)
    return check_ampeco_response(response, 200, 'update_electricity_meter')


def update_electricity_meter_mapping(conn_str, row, meter_id):
    """Insert or update circuit_mapping with target_electricity_meter_id.
    
    Uses the mapping_table/mapping_key pattern. Electricity meters share the
    circuit_mapping table with circuits (1:1 with Controllers).
    """
    with get_db_connection(conn_str) as conn:
        cursor = conn.cursor()
        
        if hasattr(row, "mapping_table") and row.mapping_table:
            table_name = row.mapping_table  # "circuit_mapping"
            query = f'''
                INSERT INTO "Mapping"."{table_name}" (mapping_key, target_electricity_meter_id)
                VALUES (?, ?)
                ON CONFLICT (mapping_key) DO UPDATE SET target_electricity_meter_id = EXCLUDED.target_electricity_meter_id
            '''
            params = (row.mapping_key, meter_id)
            success_msg = f"Upserted {table_name} target_electricity_meter_id for mapping_key: {row.mapping_key}"
        else:
            raise SystemExit("No mapping_table found on row; cannot update electricity meter mapping.")
        
        cursor.execute(query, params)
        if cursor.rowcount == 0:
            raise SystemExit(f"Upsert affected 0 rows for mapping_key: {row.mapping_key}. Halting.")
        conn.commit()
        logging.info(success_msg)


def process_electricity_meter(conn_str, row):
    """Process a single electricity meter: create or update."""
    global stats
    
    meter_id = row.target_electricity_meter_id
    
    # Build meter data payload
    meter_data = {
        "name": get_value(row.name, str),
        "integrationId": get_value(row.integrationId, int),
        "integrationParameters": {
            "device_id": get_value(row.integrationParameters_device_id, str)
        }
    }

    try:
        if meter_id is None:
            # Create new electricity meter
            response_data = create_electricity_meter(meter_data)
            meter_id = response_data['id']
            stats['created'] += 1
            update_electricity_meter_mapping(conn_str, row, meter_id)
            logging.info(f"Created electricity meter '{row.name}' with id={meter_id}")
        else:
            # Update existing electricity meter
            update_electricity_meter(meter_id, meter_data)
            stats['updated'] += 1
            logging.info(f"Updated electricity meter '{row.name}' (id={meter_id})")

    except Exception as e:
        stats['errors'].append((row, str(e)))
        logging.error(f"Error processing electricity meter '{row.name}': {e}")


def main():
    """Main function to create/update electricity meters."""
    global stats
    conn_str = get_db_connection_string()
    
    # Fetch all electricity meters
    meters = fetch_electricity_meters(conn_str)
    stats['total'] = len(meters)
    logging.info(f"Total electricity meters to process: {stats['total']}")
    
    # Process each meter
    logging.info("")
    logging.info("=" * 60)
    logging.info("Creating/updating electricity meters...")
    logging.info("=" * 60)
    for idx, row in enumerate(meters, 1):
        logging.info(f"Processing {idx} of {stats['total']}: {row.name}")
        process_electricity_meter(conn_str, row)
    
    # Summary
    logging.info("")
    logging.info("=" * 60)
    logging.info("Summary:")
    logging.info("=" * 60)
    logging.info(f"Total electricity meters: {stats['total']}")
    logging.info(f"Created: {stats['created']}")
    logging.info(f"Updated: {stats['updated']}")
    logging.info(f"Warnings: {len(stats['warnings'])}")
    logging.info(f"Errors: {len(stats['errors'])}")
    
    if stats['warnings']:
        logging.info("")
        logging.info("Warnings:")
        for row, msg in stats['warnings']:
            logging.warning(f"  {row.name}: {msg}")
    
    if stats['errors']:
        logging.info("")
        logging.info("Errors:")
        for row, msg in stats['errors']:
            logging.error(f"  {row.name}: {msg}")


if __name__ == "__main__":
    try:
        setup_logging("CreateOrUpdateElectricityMeters")
        main()
    except Exception as e:
        logging.error(f"An error occurred: {e}")
