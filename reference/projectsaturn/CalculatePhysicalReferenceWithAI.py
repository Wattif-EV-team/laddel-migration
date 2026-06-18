import logging
import openai
from openai import OpenAI
from pydantic import BaseModel
import os
import re
import csv
from io import StringIO
from collections import namedtuple 
from typing import cast
from utils.config_utils import get_db_connection_string
from utils.dbutils import get_db_connection, get_sql_dialect, quote_identifier
from utils.log_utils import setup_logging  

# Suppress OpenAI library logging
logging.getLogger('openai').setLevel(logging.ERROR)

# Create Client for OpenAI API with API key from environment variable (lazy initialization)
client = None

def get_openai_client():
    global client
    if client is None:
        client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])
    return client

# Define the Pydantic model for the structured output
class SequenceNumberResult(BaseModel):
    analyse_input: str
    selected_strategy: str
    sequence_numbers: list[str]

# Initialize counters and error list
stats = {
    'processed': 0,
    'updated': 0,
    'errors': []
}

def load_system_prompt(filename: str = "CalculatePhysicalReferenceWithAI-Promt.md") -> str:
    """Load the system prompt content from a markdown file next to this script."""
    base_dir = os.path.dirname(__file__)
    prompt_path = os.path.join(base_dir, filename)
    if not os.path.exists(prompt_path):
        raise SystemExit(f"System prompt file not found: {prompt_path}")
    with open(prompt_path, "r", encoding="utf-8") as f:
        return f.read()

def fetch_connectors(conn_str):
    """Fetch all connectors (dialect-aware). Use SELECT * and order by ProjectCode, RowNumber.

    Returns a tuple of (rows, column_names).
    """
    with get_db_connection(conn_str) as conn:
        cursor = conn.cursor()
        dialect = get_sql_dialect(conn)
        reports_schema = quote_identifier("Reports", dialect)
        table = quote_identifier("AllEvseWithPhysicalReference", dialect)
        project_code = quote_identifier("ProjectCode", dialect)
        row_number = quote_identifier("RowNumber", dialect)
        query = (
            f"SELECT * FROM {reports_schema}.{table} "
            f"ORDER BY {project_code}, {row_number}"
        )
        cursor.execute(query)
        rows = cursor.fetchall()
        column_names = [desc[0] for desc in cursor.description]
        return rows, column_names

def update_mapping(conn_str, row, physical_reference: str):
    """Update mapping tables with PhysicalReference (single-query pattern).

    - If row has SourceChargerID (Charge365/PostgreSQL), UPDATE "Mapping"."ChargerMapping" by SourceChargerID.
    - Else if row has SourceChargePointID (MSSQL), INSERT into [Mapping].[ConnectorMapping].
    """
    with get_db_connection(conn_str) as conn:
        cursor = conn.cursor()

        query = None
        params = None
        success_msg = None

        # Charge365: PostgreSQL ChargerMapping by SourceChargerID
        if hasattr(row, "SourceChargerID") and getattr(row, "SourceChargerID", None):
            query = (
                """
                UPDATE "Mapping"."ChargerMapping"
                SET "PhysicalReference" = ?
                WHERE "SourceChargerID" = ?
                """
            )
            params = (physical_reference, row.SourceChargerID)
            success_msg = (
                f"Updated PhysicalReference for SourceChargerID: {row.SourceChargerID} -> {physical_reference}"
            )
        # Default: MSSQL ConnectorMapping by SourceChargePointID (insert)
        elif hasattr(row, "SourceChargePointID"):
            query = (
                """
                INSERT INTO [Mapping].[ConnectorMapping] (SourceChargePointID, PhysicalReference)
                VALUES (?, ?)
                """
            )
            params = (row.SourceChargePointID, physical_reference)
            success_msg = (
                f"Inserted mapping for SourceChargePointID: {row.SourceChargePointID} with PhysicalReference: {physical_reference}"
            )

        if query is None or params is None:
            raise SystemExit("No valid mapping condition found; query not built. Halting execution.")

        cursor.execute(query, params)
        if cursor.rowcount == 0:
            raise SystemExit("Mapping change affected 0 rows; halting execution to prevent missing mapping.")
        conn.commit()
        logging.info(success_msg)

def process_group(conn_str, project_code, group, column_names):
    """Process a group of connectors with the same ProjectCode."""
    global stats
    logging.info(f"Processing ProjectCode: {project_code} with {len(group)} connectors")

    # Check if all connectors already have PhysicalReference
    if all(connector.PhysicalReference for connector in group):
        logging.info(f"All connectors in ProjectCode: {project_code} already have PhysicalReference. Skipping.")
        return

    # Prepare dynamic CSV content for AI call
    # - Include all columns except ProjectCode, RowNumber and PhysicalReference
    # - Add a derived SequenceNumber column extracted from PhysicalReference after ProjectCode prefix
    # - Clean SourceName of control characters
    exclude_cols = {"ProjectCode", "RowNumber", "PhysicalReference"}
    included_cols = [c for c in column_names if c not in exclude_cols]

    def clean_control_chars(value: str) -> str:
        return ''.join(ch for ch in value if ord(ch) >= 32)

    sio = StringIO()
    writer = csv.writer(sio, quoting=csv.QUOTE_MINIMAL)
    # Header row
    writer.writerow(included_cols + ["SequenceNumber"])

    for connector in group:
        proj = getattr(connector, "ProjectCode", "")
        physical = getattr(connector, "PhysicalReference", None)
        phys_str = str(physical) if physical is not None else None
        # Compute sequence number based on ProjectCode length
        if phys_str:
            if not phys_str.startswith(str(proj)):
                logging.warning(
                    f"PhysicalReference prefix mismatch for row with ProjectCode {proj}: {phys_str} does not start with ProjectCode"
                )
            sequence_number = phys_str[len(str(proj)) :]
        else:
            sequence_number = ""

        row_values = []
        for col in included_cols:
            val = getattr(connector, col, None)
            if col == "SourceName" and isinstance(val, str):
                val = clean_control_chars(val)
            # Convert None to empty string for CSV
            row_values.append("" if val is None else val)
        row_values.append(sequence_number)
        writer.writerow(row_values)

    content = sio.getvalue()
    logging.info(f"Content for OpenAI call (CSV):\n{content}")

    try:
        # Create the chat completion with the parse method
        system_prompt = load_system_prompt()
        completion = get_openai_client().beta.chat.completions.parse(
            model="gpt-5",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": content},
            ],
            response_format=SequenceNumberResult,
            temperature=1.0,
        )
        # Extract the parsed result
        parsed_any = completion.choices[0].message.parsed
        if parsed_any is None:
            raise ValueError("OpenAI response did not contain parsed content")
        parsed_result = cast(SequenceNumberResult, parsed_any)

        logging.info(f"Analyse Input: {parsed_result.analyse_input}")
        logging.info(f"Selected Strategy: {parsed_result.selected_strategy}")
        logging.info(f"Sequence Numbers:\n{'\n'.join(parsed_result.sequence_numbers)}")

        # Validate output
        if len(parsed_result.sequence_numbers) != len(group):
            raise ValueError("Number of returned items does not match the number of connectors")

        sequence_numbers_set = set()
        for connector, sequence_number in zip(group, parsed_result.sequence_numbers):
            if connector.PhysicalReference:
                expected_seq = str(connector.PhysicalReference)[len(str(connector.ProjectCode)) :]
                if expected_seq != sequence_number:
                    raise ValueError(f"Mismatch in PhysicalReference for connector {getattr(connector, 'SourceChargePointID', 'N/A')}")
            if not re.match(r'^\d{3}[A-Z]?$|^\d{3}$', sequence_number):
                raise ValueError(f"Invalid SequenceNumber format: {sequence_number}")
            if sequence_number in sequence_numbers_set:
                raise ValueError(f"Duplicate SequenceNumber: {sequence_number} within group")
            sequence_numbers_set.add(sequence_number)

        # Update database with new PhysicalReference
        for connector, sequence_number in zip(group, parsed_result.sequence_numbers):
            if not connector.PhysicalReference:
                physical_reference = f"{connector.ProjectCode}{sequence_number}"
                update_mapping(conn_str, connector, physical_reference)
                stats['updated'] += 1

    except Exception as e:
        stats['errors'].append((project_code, str(e)))
        logging.error(f"Error processing ProjectCode {project_code}: {e}")

def main():
    """Main function to fetch and process connectors."""
    global stats
    conn_str = get_db_connection_string()  # Get connection string
    rows, column_names = fetch_connectors(conn_str)
    total_rows = len(rows)
    logging.info(f"Total connectors fetched: {total_rows}")

    # Group connectors by ProjectCode
    from itertools import groupby
    rows.sort(key=lambda x: getattr(x, 'ProjectCode', ''))
    grouped_rows = [(key, list(group)) for key, group in groupby(rows, key=lambda x: getattr(x, 'ProjectCode', ''))]
    total_groups = len(grouped_rows)

    for index, (project_code, group) in enumerate(grouped_rows, start=1):
        logging.info(f"Processing group {index} of {total_groups} (ProjectCode: {project_code})...")
        process_group(conn_str, project_code, group, column_names)
        stats['processed'] += len(group)

    # Summarize results
    logging.info(f"Processed: {stats['processed']}")
    logging.info(f"Updated: {stats['updated']}")
    logging.info(f"Errors: {len(stats['errors'])}")
    for error in stats['errors']:
        logging.error(f"Error processing ProjectCode {error[0]}: {error[1]}")

if __name__ == "__main__":
    setup_logging("CalculatePhysicalReferenceWithAI")
    try:
        main()
    except Exception as e:
        logging.error(f"An error occurred: {e}")
