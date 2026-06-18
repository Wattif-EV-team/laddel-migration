import sys
import logging
import os
import glob
import asyncio
import csv
import re
from dotenv import load_dotenv
import click

from utils.log_utils import setup_logging
from utils.config_utils import get_db_connection_string
from utils.dbutils import get_db_connection, get_sql_dialect
from FetchFromSharePoint import main as fetch_from_sharepoint_main
from FetchFromTeltonika import main as fetch_from_teltonika_main

load_dotenv()

DDL_DIRECTORY = "db/project-sleet"


def _format_ddl_error(sql_content, file_name, error):
    """Format a DDL error with file position context when available.
    
    Parses STATEMENT_POSITION from the psqlODBC error message (requires
    OptionalErrors=1 in the connection string) and shows the relevant
    lines from the SQL file with a pointer to the error location.
    """
    error_msg = str(error)
    
    # Extract STATEMENT_POSITION from the psqlODBC optional error fields
    pos_match = re.search(r'STATEMENT_POSITION:\s*(\d+)', error_msg)
    if not pos_match:
        # No position info available — log the raw error only
        logging.error(f"Error in {file_name}: {error_msg}")
        return
    
    char_pos = int(pos_match.group(1))  # 1-based character offset
    if char_pos < 1 or char_pos > len(sql_content):
        logging.error(f"Error in {file_name}: {error_msg}")
        return
    
    # Convert character position to line/column
    prefix = sql_content[:char_pos - 1]
    err_line = prefix.count('\n') + 1
    last_nl = prefix.rfind('\n')
    err_col = (char_pos - 1) - last_nl  # 1-based
    
    # Build the primary error line (strip the STATEMENT_POSITION noise)
    primary_msg = re.sub(r'\s*STATEMENT_POSITION:\s*\d+', '', error_msg).strip()
    logging.error(f"Error in {file_name} at line {err_line}, column {err_col}:")
    logging.error(f"  {primary_msg}")
    
    # Show context: up to 3 lines before, the error line with marker, up to 3 after
    lines = sql_content.splitlines()
    ctx_start = max(0, err_line - 4)      # 0-based index
    ctx_end = min(len(lines), err_line + 3)
    
    logging.error("")
    for idx in range(ctx_start, ctx_end):
        line_num = idx + 1
        marker = " >>>" if line_num == err_line else "    "
        logging.error(f"  {marker} {line_num:4d} | {lines[idx]}")
        if line_num == err_line:
            logging.error(f"         {' ' * err_col}^")
    logging.error("")

SLEET_DATA_FILES = [
    "Sleet Active Locations NO_PriceList_03.02 1.xlsx",
    "Sleet - Main Active Controllers 04_02_2026 12_12_25.xlsx",
    "Sleet Active Clusters 04_02_2026 11_07_21.xlsx",
    "Sleet Active Chargers NO 13_02_2026 13_05_20.xlsx",
    "Sleet Main - Connectors 13_02_2026 13_06_38.xlsx",
    "Project Sleet Planning - Master.xlsx [EVSE_ID]",  # EVSE_ID table for physical reference lookup
    # v3 Tariff/User tables
    "ChargerToChargerGroup_Data_29_01.xlsx",
    "Charger_User_All_Details_14-04-2026 - Signert pdd.xlsx",
    "Charger_User_Glencore 27.04.xlsx",
    "Sleet Tariffs_03_02.xlsx [PriceListItems]",
    "Sleet Tariffs_03_02.xlsx [PriceList]",
    "Sleet Tariffs_03_02.xlsx [PriceToUsersAndChargers]",
    # Corporate billing RFID tag files
    "Sleet Active RFID Tags_Gardermoen Leiebilservice AS _19.02.2026.xlsx",
    "Active RFID Tags 24_02_2026 10_29_09.xlsx",
    "Sleet Active RFID Tags 04_03_2026 12_36_29.xlsx",
    # Routers and Meters from Master planning file (re-imported from report sheets)
    "Project Sleet Planning - Master.xlsx [Routers]",
    "Project Sleet Planning - Master.xlsx [Meters]",
]

TARGET_VIEWS = [
    '"Target"."Users"',
    '"Target"."IdTags"',
    '"Target"."Partners"',
    '"Target"."PartnerContracts"',
    '"Target"."Locations"',
    '"Target"."ChargingZones"',
    '"Target"."UserGroups"',
    '"Target"."UserGroupMembers"',
    '"Target"."SubscriptionPlan"',
    '"Target"."TariffGroupsAndBaseTariff"',
    '"Target"."Tariff_Simple"',
    '"Target"."ChargePoints"',
    '"Target"."EvseAndConnectors"',
    '"Target"."Circuits"',
    '"Target"."ChargePointCircuitAttachment"',
    '"Target"."PartnerInvites"',
    '"Target"."PartnerAdmins"',
    '"Reports"."AllEvseWithPhysicalReference"',
    '"Reports"."CircuitQualityIssues"',
    '"Reports"."PerificOrganisationsMasked"',
    '"Reports"."PerificOrganisationsUnmasked"',
    '"Reports"."TeltonikaRouters"',
    '"Reports"."ElectricityMeters"',
    '"Reports"."PartnerAdmins"',
    '"Target"."SiteTrackerSites"',
    '"Target"."SiteTrackerAccounts"',
    '"Target"."SiteTrackerSiteRelations"',
    '"Target"."SiteTrackerFieldAssets"',
]


def run_ddl_files(ddl_file=None):
    """Execute DDL files in numbered order or a specific file."""
    conn_str = get_db_connection_string()
    if not conn_str:
        logging.error("Database connection string is not configured")
        sys.exit(1)
    
    # Discover DDL files
    if ddl_file:
        sql_files = [os.path.join(DDL_DIRECTORY, ddl_file)]
        if not os.path.exists(sql_files[0]):
            logging.error(f"DDL file not found: {sql_files[0]}")
            sys.exit(1)
    else:
        pattern = os.path.join(DDL_DIRECTORY, "*.sql")
        sql_files = sorted(glob.glob(pattern))
    
    if not sql_files:
        logging.warning(f"No SQL files found in {DDL_DIRECTORY}")
        return
    
    logging.info(f"Found {len(sql_files)} DDL file(s) to execute")
    
    conn = get_db_connection(conn_str)
    _ = get_sql_dialect(conn)  # Verify connection works
    
    try:
        cursor = conn.cursor()
        
        for sql_file in sql_files:
            file_name = os.path.basename(sql_file)
            logging.info(f"Executing DDL file: {file_name}")
            
            # Read SQL file
            with open(sql_file, 'r', encoding='utf-8') as f:
                sql_content = f.read()
            
            # Ensure role is set (prepend if not present)
            if 'SET ROLE' not in sql_content.upper():
                sql_content = "SET ROLE db_sleetmigration_owner;\n\n" + sql_content
            
            # PostgreSQL can handle multiple statements in one batch
            batches = [sql_content]
            
            # Execute each batch
            for i, batch in enumerate(batches):
                batch = batch.strip()
                if not batch:
                    continue
                
                try:
                    logging.debug(f"Executing batch {i+1}/{len(batches)}")
                    cursor.execute(batch)
                    conn.commit()
                    
                    if cursor.rowcount >= 0:
                        logging.debug(f"Batch {i+1} executed successfully (rows affected: {cursor.rowcount})")
                    else:
                        logging.debug(f"Batch {i+1} executed successfully")
                        
                except Exception as e:
                    conn.rollback()
                    _format_ddl_error(batch, file_name, e)
                    raise
            
            logging.info(f"✓ Successfully executed {file_name}")
        
        cursor.close()
        conn.close()
        logging.info("✓ All DDL files executed successfully")
        
    except Exception as e:
        logging.error(f"DDL execution failed: {e}")
        if conn:
            conn.close()
        raise


def import_sleet_data_files():
    """Import all Sleet operational data files from SharePoint.
    
    This is slow (~10+ min) due to large tables like RawChargerUsers (122k rows).
    To reload a single file, use: python FetchFromSharePoint.py "<file name>"
    """
    for file_name in SLEET_DATA_FILES:
        logging.info(f"Importing: {file_name}")
        asyncio.run(fetch_from_sharepoint_main(file_name))


def import_master_file():
    """Import the Master planning file from SharePoint.
    
    Post-import SQL (in sharepoint_sync.json) copies planning data from
    ExcelPlanningData into Mapping.location_mapping. Views depend on
    location_mapping (permanent), not ExcelPlanningData (drop-and-recreate),
    so they are not affected by this import.
    """
    master_file = os.getenv('sharepoint_file_name')
    if not master_file:
        raise ValueError("Missing 'sharepoint_file_name' in .env file for Master import")
    
    logging.info(f"Importing Master file: {master_file}")
    asyncio.run(fetch_from_sharepoint_main(master_file))


def check_quality_reports():
    """Check quality report views and emit warnings/errors for issues found.
    
    Returns tuple (warning_count, error_count) for summary reporting.
    """
    conn_str = get_db_connection_string()
    if not conn_str:
        logging.error("Database connection string is not configured")
        return (0, 0)
    
    conn = get_db_connection(conn_str)
    cursor = conn.cursor()
    
    logging.info("")
    logging.info("=" * 60)
    logging.info("Quality Reports:")
    logging.info("=" * 60)
    
    total_info = 0
    total_warnings = 0
    total_errors = 0
    
    # List of quality report views to check
    quality_reports = [
        ("CircuitQualityIssues", "Reports"),
        ("IdTagQualityIssues", "Reports"),
    ]
    
    for report_name, schema in quality_reports:
        try:
            # Get counts by classification and issue_type
            cursor.execute(f'''
                SELECT classification, issue_type, COUNT(*) as cnt
                FROM "{schema}"."{report_name}"
                GROUP BY classification, issue_type
                ORDER BY 
                    CASE classification WHEN 'ERROR' THEN 1 WHEN 'WARNING' THEN 2 ELSE 3 END,
                    issue_type
            ''')
            
            rows = cursor.fetchall()
            if rows:
                logging.info(f"  {report_name}:")
                for row in rows:
                    classification, issue_type, count = row.classification, row.issue_type, row.cnt
                    if classification == 'ERROR':
                        total_errors += count
                        logging.error(f"    [{classification}] {issue_type}: {count} issues")
                    elif classification == 'WARNING':
                        total_warnings += count
                        logging.warning(f"    [{classification}] {issue_type}: {count} issues")
                    else:  # INFO
                        total_info += count
                        logging.info(f"    [{classification}] {issue_type}: {count} issues")
            else:
                logging.info(f"  {report_name}: No issues found")
                
        except Exception as e:
            logging.error(f"  {report_name}: ERROR checking report - {e}")
    
    # Summary
    logging.info("")
    if total_errors > 0:
        logging.error(f"Quality Report Summary: {total_info} info, {total_warnings} warnings, {total_errors} errors")
    elif total_warnings > 0:
        logging.warning(f"Quality Report Summary: {total_info} info, {total_warnings} warnings, {total_errors} errors")
    else:
        logging.info(f"Quality Report Summary: {total_info} info, {total_warnings} warnings, {total_errors} errors")
    
    cursor.close()
    conn.close()
    
    return (total_warnings, total_errors)


def output_view_counts():
    """Query and output row counts for all Target views."""
    conn_str = get_db_connection_string()
    if not conn_str:
        logging.error("Database connection string is not configured")
        return
    
    conn = get_db_connection(conn_str)
    cursor = conn.cursor()
    
    logging.info("")
    logging.info("=" * 60)
    logging.info("Target View Row Counts:")
    logging.info("=" * 60)
    
    for view in TARGET_VIEWS:
        try:
            cursor.execute(f'SELECT COUNT(*) FROM {view}')
            count = cursor.fetchone()[0]
            logging.info(f"  {view}: {count}")
        except Exception as e:
            logging.error(f"  {view}: ERROR - {e}")
    
    # Migration stats
    logging.info("")
    logging.info("Migration Stats:")
    cursor.execute('SELECT COUNT(*) FROM "Mapping"."location_mapping" WHERE migrate = TRUE')
    migrate_count = cursor.fetchone()[0]
    logging.info(f"  Locations with migrate=TRUE: {migrate_count}")
    
    cursor.execute('SELECT COUNT(DISTINCT project_code) FROM "Mapping"."location_mapping" WHERE migrate = TRUE')
    project_count = cursor.fetchone()[0]
    logging.info(f"  Distinct project_codes with migrate=TRUE: {project_count}")
    
    cursor.close()
    conn.close()


def export_view_to_csv(view_name, output_file):
    """Export a view or table to CSV file.
    
    Args:
        view_name: View/table name in format 'Schema.ViewName' (without quotes)
        output_file: Output file path (absolute or relative to working directory)
    """
    # Parse view name and add quotes
    parts = view_name.split('.')
    if len(parts) != 2:
        logging.error(f"Invalid view name format: {view_name}. Expected 'Schema.ViewName'")
        sys.exit(1)
    
    quoted_view = f'"{parts[0]}"."{parts[1]}"'
    
    conn_str = get_db_connection_string()
    if not conn_str:
        logging.error("Database connection string is not configured")
        sys.exit(1)
    
    conn = get_db_connection(conn_str)
    cursor = conn.cursor()
    
    try:
        logging.info(f"Exporting {quoted_view} to {output_file}")
        cursor.execute(f'SELECT * FROM {quoted_view}')
        
        # Get column names from cursor description
        columns = [desc[0] for desc in cursor.description]
        rows = cursor.fetchall()
        
        # Write CSV with UTF-8 BOM for Excel compatibility
        with open(output_file, 'w', newline='', encoding='utf-8-sig') as f:
            writer = csv.writer(f, quoting=csv.QUOTE_ALL, doublequote=True)
            writer.writerow(columns)
            for row in rows:
                writer.writerow(row)
        
        logging.info(f"✓ Exported {len(rows)} rows to {output_file}")
        
    except Exception as e:
        logging.error(f"Export failed: {e}")
        raise
    finally:
        cursor.close()
        conn.close()


@click.command()
@click.option('--ddl-file', help='Specific DDL file to execute (e.g., 301_target_users.sql). If omitted, runs all files in order.')
@click.option('--ddl-only', is_flag=True, help='Only run DDL files, skip data import.')
@click.option('--import-master', is_flag=True, help='Only import Master planning file from SharePoint.')
@click.option('--import-sleet-data', is_flag=True, help='Only import 5 Sleet operational data files from SharePoint.')
@click.option('--import-teltonika', is_flag=True, help='Only import devices from Teltonika RMS API.')
@click.option('--export-view', help='Export a view/table to CSV. Format: Schema.ViewName (e.g., Reports.PerificOrganisationsMasked)')
@click.option('--export-file', help='Output CSV file path for --export-view.')
def main(ddl_file, ddl_only, import_master, import_sleet_data, import_teltonika, export_view, export_file):
    """
    Build Project Sleet database by importing data and executing DDL files.
    
    Without any options, runs the full pipeline:
      1. Import Sleet operational data (5 files)
      2. Import Teltonika RMS devices
      3. Execute all DDL files
      4. Import Master planning file
      5. Output row counts for all views
    
    Examples:
        # Run full pipeline (recommended)
        python BuildProjectSleetDatabase.py
        
        # Only run DDL files
        python BuildProjectSleetDatabase.py --ddl-only
        
        # Run a specific DDL file
        python BuildProjectSleetDatabase.py --ddl-only --ddl-file 303_target_partners.sql
        
        # Only import Master planning file
        python BuildProjectSleetDatabase.py --import-master
        
        # Only import Sleet operational data (5 files)
        python BuildProjectSleetDatabase.py --import-sleet-data
        
        # Only import Teltonika RMS devices
        python BuildProjectSleetDatabase.py --import-teltonika
        
        # Export a view to CSV
        python BuildProjectSleetDatabase.py --export-view Reports.PerificOrganisationsMasked --export-file data/perific_masked.csv
    """
    setup_logging("BuildProjectSleetDatabase")
    
    logging.info("=" * 80)
    logging.info("Project Sleet Database Builder")
    logging.info("=" * 80)
    
    # Handle export operation separately (can be combined with other operations)
    if export_view:
        if not export_file:
            logging.error("--export-file is required when using --export-view")
            sys.exit(1)
        try:
            export_view_to_csv(export_view, export_file)
        except Exception as e:
            logging.error(f"Export failed: {e}")
            sys.exit(1)
        # If only exporting, exit early
        if not (ddl_only or import_master or import_sleet_data):
            logging.info("")
            logging.info("=" * 80)
            logging.info("Export completed successfully")
            logging.info("=" * 80)
            return
    
    # Determine if running full pipeline or specific operation
    specific_operation = ddl_only or import_master or import_sleet_data or import_teltonika
    
    if not specific_operation:
        # Full pipeline: Load data -> Build DDL -> Load Master -> Output counts -> Quality reports
        try:
            logging.info("")
            logging.info("Step 1/6: Importing Sleet operational data...")
            import_sleet_data_files()
            
            logging.info("")
            logging.info("Step 2/6: Importing Teltonika RMS devices...")
            fetch_from_teltonika_main()
            
            logging.info("")
            logging.info("Step 3/6: Executing DDL files...")
            run_ddl_files(ddl_file)
            
            logging.info("")
            logging.info("Step 4/6: Importing Master planning file...")
            import_master_file()
            
            logging.info("")
            logging.info("Step 5/6: Outputting view counts...")
            output_view_counts()
            
            logging.info("")
            logging.info("Step 6/6: Checking quality reports...")
            check_quality_reports()
            
        except Exception as e:
            logging.error(f"Pipeline failed: {e}")
            sys.exit(1)
    else:
        # Specific operations
        if import_sleet_data:
            try:
                import_sleet_data_files()
            except Exception as e:
                logging.error(f"Failed to import Sleet data: {e}")
                sys.exit(1)
        
        if ddl_only:
            try:
                run_ddl_files(ddl_file)
                output_view_counts()
                check_quality_reports()
            except Exception as e:
                logging.error(f"Failed to execute DDL files: {e}")
                sys.exit(1)
        
        if import_teltonika:
            try:
                fetch_from_teltonika_main()
            except Exception as e:
                logging.error(f"Failed to import Teltonika devices: {e}")
                sys.exit(1)
        
        if import_master:
            try:
                import_master_file()
            except Exception as e:
                logging.error(f"Failed to import Master file: {e}")
                sys.exit(1)
    
    logging.info("")
    logging.info("=" * 80)
    logging.info("Build completed successfully")
    logging.info("=" * 80)


if __name__ == "__main__":
    main()
