import asyncio
import numpy as np
import pandas as pd
import logging
import json
import re
import sys
from email_validator import validate_email, EmailNotValidError, caching_resolver
from utils.graph_client import GraphClient
from utils.dbutils import save_to_database_table, get_db_connection
from utils.config_utils import get_db_connection_string
from utils.log_utils import setup_logging
from dotenv import load_dotenv
import os

# Global constant to bypass all confirmations
BYPASS_CONFIRMATION = True

# Load environment variables
load_dotenv()

# Load workbook configuration from JSON file with correct encoding
with open('sharepoint_sync.json', encoding='utf-8') as config_file:
    WORKBOOKS = json.load(config_file)

# Create a caching DNS resolver
resolver = caching_resolver(timeout=10)

async def main(workbook_name=None):
    logging.info("Starting data fetch from Excel...")
    graph_client = GraphClient()

    try:
        await _run_sharepoint_fetch(graph_client, workbook_name)
    finally:
        # Close the underlying httpx.AsyncClient to avoid dangling tasks on
        # asyncio event-loop shutdown (Python 3.13+ raises KeyboardInterrupt).
        http_client = getattr(
            getattr(graph_client.user_client, 'request_adapter', None),
            '_http_client', None
        )
        if http_client is not None:
            await http_client.aclose()


async def _run_sharepoint_fetch(graph_client, workbook_name=None):
    # Fetch default workbook name from .env
    default_workbook_name = os.getenv('sharepoint_file_name')

    # List all known data tables
    print("Available data tables:")
    for workbook in WORKBOOKS:
        print(f"- {workbook['name']}")

    # Prompt user to select a valid table if not provided
    selected_table = None
    if workbook_name:
        for workbook in WORKBOOKS:
            if workbook['name'].lower() == workbook_name.lower():
                selected_table = workbook
                break
        if not selected_table:
            print(f"Invalid table name: {workbook_name}")
            return
    else:
        while not selected_table:
            table_name = input(f"Please enter the name of the data table to fetch [{default_workbook_name}]: ").strip()
            if not table_name:
                table_name = default_workbook_name  # Use default if no input is provided
            for workbook in WORKBOOKS:
                workbook_name = workbook.get('name')
                if workbook_name and table_name and workbook_name.lower() == table_name.lower():
                    selected_table = workbook
                    break
            if not selected_table:
                print("Invalid table name. Please try again.")

    df = await graph_client.fetch_table_data(selected_table['drive_id'], selected_table['file_id'], selected_table['table_name'])
    if df is not None:
        logging.info("Data fetched successfully. Processing data frame...")
        df = process_data_frame(df, selected_table['columns'])
        validate_data_frame(df, selected_table['columns'])
        conn_str = get_db_connection_string()  # Fetch connection string
        if not conn_str:
            raise ValueError("Database connection string is not configured")
        
        # Handle tables without a natural primary key by generating a synthetic row_id
        primary_key = selected_table.get('primary_key')
        if primary_key is None:
            df.insert(0, '_row_id', range(1, len(df) + 1))
            primary_key = '_row_id'
            logging.info(f"Generated synthetic primary key '_row_id' for {len(df)} rows")
        
        with get_db_connection(conn_str) as conn:  # Use get_db_connection
            logging.info("Saving data to database...")
            save_to_database_table(
                conn_str,
                selected_table['database_schema'],
                selected_table['database_table'],
                df,
                primary_key,
                remove_empty_primary_keys=selected_table.get('remove_empty_primary_keys', False),
                database_role=selected_table.get('database_role')
            )
            logging.info("Data saved to database successfully.")
            await execute_post_import_sql_commands(conn, selected_table.get('post_import_sql_commands', []))


    else:
        logging.warning("No data fetched from Excel.")

async def execute_post_import_sql_commands(conn, sql_commands):
    cursor = conn.cursor()
    try:
        for command in sql_commands:
            logging.info(f"SQL Command: {command['name']}")
            logging.info(f"Description: {command['description']}")
            
            sql = command['sql']
            if isinstance(sql, list):
                sql = " ".join(sql)
            
            logging.info(f"SQL: {sql}")
            
            if not BYPASS_CONFIRMATION:
                while True:
                    user_input = input("Do you want to execute this command? (Y/N): ").strip().upper()
                    if user_input in ['Y', 'N']:
                        break
                    print("Invalid input. Please enter 'Y' or 'N'.")
                if user_input == 'N':
                    continue
            
            try:
                cursor.execute(sql)
                conn.commit()
                logging.info(f"Command '{command['name']}' executed successfully. Rows affected: {cursor.rowcount}")
            except Exception as e:
                logging.error(f"Error executing command '{command['name']}': {e}")
                raise  # Stop execution on error
    except Exception as e:
        logging.error("Post-import SQL command execution failed. Stopping further execution.")
        logging.error(f"Final error: {e}")
        raise

def process_data_frame(df, column_metadata):
    """
    Process the DataFrame based on column metadata.
    """
    missing_columns = [col['excel_name'] for col in column_metadata if col['excel_name'] not in df.columns]
    if missing_columns:
        raise Exception(f"Missing mandatory columns: {', '.join(missing_columns)}")

    df = df.loc[:, [col['excel_name'] for col in column_metadata]]

    for col in column_metadata:
        if col['data_type'] == 'date':
            logging.info(f"Processing date column: {col['excel_name']}")
            df.loc[:, col['excel_name']] = df[col['excel_name']].apply(lambda x: convert_excel_date(x))
        elif col['data_type'] == 'text':
            logging.info(f"Processing text column: {col['excel_name']}")
            df.loc[:, col['excel_name']] = df[col['excel_name']].astype(str)
        elif col['data_type'] == 'integer':
            logging.info(f"Processing integer column: {col['excel_name']}")
            # Replace empty strings and invalid values with <NA>
            df[col['excel_name']] = (
                df[col['excel_name']]
                .replace(r'^\s*$', pd.NA, regex=True)  # Replace empty strings or whitespace with <NA>
                .replace(['null', 'None'], pd.NA)  # Replace null-like strings with <NA>
            )
            df[col['excel_name']] = pd.Series(df[col['excel_name']], dtype="Int64")
        elif col['data_type'] == 'decimal':
            logging.info(f"Processing decimal column: {col['excel_name']}")
            def _to_float(val):
                if val is None or (isinstance(val, float) and pd.isna(val)):
                    return np.nan
                if isinstance(val, str):
                    val = val.strip()
                    if val == '':
                        return np.nan
                    try:
                        return float(val)
                    except ValueError:
                        logging.warning(f"Cannot parse '{val}' as decimal in column {col['excel_name']}")
                        return np.nan
                return val
            df.loc[:, col['excel_name']] = df[col['excel_name']].apply(_to_float)
            df[col['excel_name']] = pd.to_numeric(df[col['excel_name']], errors='coerce')

    df.columns = [col['db_name'] for col in column_metadata]
    return df

def validate_data_frame(df, column_metadata):
    for col in column_metadata:
        if col.get('data_check') == 'email':
            logging.info(f"Validating email column: {col['excel_name']}")
            for index, value in df[col['db_name']].items():
                emails = [email.strip() for email in re.split(r'[;,]', value) if email.strip()]
                invalid_emails = []
                for email in emails:
                    try:
                        validate_email(email, check_deliverability=True, dns_resolver=resolver)
                    except EmailNotValidError as e:
                        invalid_emails.append(email)
                if invalid_emails:
                    logging.warning(f"Invalid email(s) found in row {index + 1}: {', '.join(invalid_emails)}")

def convert_excel_date(value):
    if pd.isna(value):
        return None
    if isinstance(value, (int, float)) and 1 <= value <= 73050:
        return pd.to_datetime(value, unit='D', origin='1899-12-30').strftime('%Y-%m-%d')
    return value

if __name__ == "__main__":
    setup_logging("FetchFromSharePoint")
    workbook_name = sys.argv[1] if len(sys.argv) > 1 else None
    asyncio.get_event_loop().run_until_complete(main(workbook_name))