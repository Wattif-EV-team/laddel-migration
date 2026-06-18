import pyodbc
import pandas as pd
import json
import logging
import re
import unicodedata
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple
from tenacity import retry, stop_after_attempt, wait_exponential
from time import perf_counter

DEFAULT_INSERT_BATCH_SIZE = 1000
MAX_RECONNECT_ATTEMPTS = 3


class SqlDialect(Enum):
    SQL_SERVER = "Microsoft SQL Server"
    POSTGRESQL = "PostgreSQL"

    @property
    def is_sql_server(self) -> bool:
        return self is SqlDialect.SQL_SERVER

    @property
    def is_postgresql(self) -> bool:
        return self is SqlDialect.POSTGRESQL

    @staticmethod
    def from_string(raw_name: str) -> "SqlDialect":
        if not raw_name:
            raise ValueError("Unable to determine SQL dialect from empty name")
        if "Postgre" in raw_name:
            return SqlDialect.POSTGRESQL
        if "SQL Server" in raw_name or "Microsoft SQL Server" in raw_name:
            return SqlDialect.SQL_SERVER
        raise ValueError(f"Unsupported SQL dialect: {raw_name}")

GUID_RE = re.compile(
    r'^[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-'
    r'[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}$'
)

### Dialect Helpers

def get_sql_dialect(conn) -> SqlDialect:
    """Detect the SQL dialect from the connection and return an enum value."""
    raw = conn.getinfo(pyodbc.SQL_DBMS_NAME)
    return SqlDialect.from_string(raw)

def quote_identifier(name: str, dialect: SqlDialect) -> str:
    """Quote an identifier (table/column) for the given SQL dialect."""
    return f'"{name}"' if dialect.is_postgresql else f'[{name}]'

def map_sql_type(dtype, is_array: bool, dialect: SqlDialect, col_name: Optional[str] = None, df_col=None) -> str:
    """Maps pandas dtype and metadata to the appropriate SQL type for the dialect."""

    if is_array:
        return "JSONB" if dialect.is_postgresql else "NVARCHAR(MAX)"

    if (
        dtype == "object"
        and df_col is not None
        and not df_col.dropna().empty
        and df_col.dropna().astype(str).apply(lambda x: bool(GUID_RE.match(x))).all()
    ):
        return "UUID" if dialect.is_postgresql else "UNIQUEIDENTIFIER"

    if pd.api.types.is_integer_dtype(dtype):
        if df_col is not None and not df_col.dropna().empty:
            min_val = df_col.min()
            max_val = df_col.max()
            if pd.notna(min_val) and pd.notna(max_val) and (min_val < -2**31 or max_val > 2**31 - 1):
                return "BIGINT"
        return "INTEGER" if dialect.is_postgresql else "INT"

    if pd.api.types.is_float_dtype(dtype):
        return "DOUBLE PRECISION" if dialect.is_postgresql else "FLOAT"

    if pd.api.types.is_bool_dtype(dtype):
        return "BOOLEAN" if dialect.is_postgresql else "BIT"

    if pd.api.types.is_datetime64_any_dtype(dtype):
        return "TIMESTAMP" if dialect.is_postgresql else "DATETIME"

    if dtype == "object":
        if col_name == "Id" and df_col is not None and not df_col.dropna().empty:
            max_length = df_col.dropna().astype(str).map(len).max()
            if pd.notna(max_length):
                return f"CHAR({int(max_length)})"
        return "TEXT" if dialect.is_postgresql else "NVARCHAR(MAX)"

    return "TEXT" if dialect.is_postgresql else "NVARCHAR(MAX)"

### Utility Functions

@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=4, max=10))
def get_db_connection(conn_str: str):
    """
    Returns a database connection using the provided connection string.
    """
    conn = pyodbc.connect(conn_str)
    logging.debug(f"Connected to database of type: '{conn.getinfo(pyodbc.SQL_DBMS_NAME)}'.")
    return conn

def analyze_schema(df: pd.DataFrame, dialect: SqlDialect) -> List[Dict]:
    """
    Analyze the schema of the DataFrame and generate metadata for SQL schema creation and data processing.
    """
    schema = []
    for col in df.columns:
        dtype = df[col].dtype
        is_array = df[col].apply(lambda x: isinstance(x, list)).any()
        is_empty = df[col].isnull().all()

        if is_empty:
            logging.info(f"Skipping empty column: {col}")
            continue

        # Special handling for integer-like columns with NaN
        if pd.api.types.is_float_dtype(dtype) and df[col].dropna().apply(float.is_integer).all():
            dtype = "int64"  # Treat as integer

        sql_type = map_sql_type(dtype, bool(is_array), dialect, col_name=col, df_col=df[col])

        column_metadata = {
            "name": col,
            "dtype": dtype,
            "is_array": is_array,
            "sql_type": sql_type,
        }
        schema.append(column_metadata)
    
    return schema

def generate_create_table_sql(schema_name: str, table_name: str, schema: List[Dict], primary_key: str, dialect: SqlDialect) -> str:
    """
    Generate SQL to drop and recreate a table based on the schema metadata.
    """
    q = lambda n: quote_identifier(n, dialect)
    columns_sql = ",\n    ".join([
        f"{q(col['name'])} {col['sql_type']} {'PRIMARY KEY NOT NULL' if col['name'] == 'Id' else ''}".strip()
        for col in schema
    ])
    if dialect.is_postgresql:
        create_table_sql = f"""
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_namespace WHERE nspname = '{schema_name}') THEN
        EXECUTE 'CREATE SCHEMA {schema_name}';
    END IF;
END$$;

DROP TABLE IF EXISTS {q(schema_name)}.{q(table_name)} CASCADE;

CREATE TABLE {q(schema_name)}.{q(table_name)} (
    {columns_sql}
);
"""
    else:
        create_table_sql = f"""
IF NOT EXISTS (SELECT * FROM sys.schemas WHERE name = '{schema_name}')
    EXEC('CREATE SCHEMA {schema_name}');

IF OBJECT_ID('{schema_name}.{table_name}', 'U') IS NOT NULL
    DROP TABLE {q(schema_name)}.{q(table_name)};

CREATE TABLE {q(schema_name)}.{q(table_name)} (
    {columns_sql}
);
"""
    return create_table_sql

def prepare_row(row, schema: List[Dict]) -> List:
    """
    Prepare a row for insertion based on schema metadata.
    Converts arrays to JSON strings and ensures NaN/None values are handled.
    """
    prepared_row = []
    for col_meta in schema:
        value = row[col_meta["name"]]

        # Handle arrays first
        if col_meta["is_array"]:
            if value is None or (isinstance(value, float) and pd.isnull(value)):
                prepared_row.append("[]")  # Default to empty JSON array
            else:
                prepared_row.append(json.dumps(value))  # Convert list to JSON
        elif pd.isnull(value):  # Handle NaN/None values
            prepared_row.append(None)
        else:
            prepared_row.append(value)  # Pass the value as is
        
    return prepared_row

def batch_insert_data_into_table(
    conn_str: str,
    schema_name: str,
    table_name: str,
    df: pd.DataFrame,
    schema: List[Dict],
    dialect: SqlDialect,
    batch_size: int = DEFAULT_INSERT_BATCH_SIZE,
    max_reconnect_attempts: int = MAX_RECONNECT_ATTEMPTS,
) -> int:
    """
    Insert data into a SQL Server or PostgreSQL table using multi-row INSERT statements.
    
    This is an optimized alternative for PostgreSQL that builds INSERT statements like:
    INSERT INTO table (col1, col2) VALUES (?, ?), (?, ?), (?, ?)
    
    This approach is faster than executemany for PostgreSQL (2-3x improvement).
    
    Note: PostgreSQL has a limit of ~34,000 parameters per statement (varies by version).
    The batch_size is automatically adjusted to stay under this limit.
    
    Returns the total number of rows inserted.
    """
    
    total_rows = len(df)
    if total_rows == 0:
        logging.info(f"No rows to insert into table '{table_name}'.")
        return 0
    
    # PostgreSQL/pyodbc has issues with very large multi-row INSERT statements
    # Limit based on both parameter count and practical SQL statement size
    num_cols = len(schema)
    
    if dialect.is_postgresql:
        # For PostgreSQL through pyodbc, use much smaller batches to avoid sync issues
        # The "lost synchronization" error occurs with large SQL statements
        MAX_PARAMS_PER_STATEMENT = 5000  # Very conservative for pyodbc+PostgreSQL
    else:
        # SQL Server can handle larger batches efficiently
        MAX_PARAMS_PER_STATEMENT = 30000
    
    # Adjust batch size to stay under parameter limit
    max_rows_per_batch = MAX_PARAMS_PER_STATEMENT // num_cols
    if batch_size > max_rows_per_batch:
        adjusted_batch_size = max(1, max_rows_per_batch)  # Ensure at least 1
        logging.info(f"Adjusted batch size from {batch_size} to {adjusted_batch_size} "
                    f"(limit: {MAX_PARAMS_PER_STATEMENT} params, {num_cols} columns)")
    else:
        adjusted_batch_size = batch_size
    
    def build_multi_row_insert_sql(active_dialect: SqlDialect, num_rows: int) -> str:
        """Build INSERT statement with multiple value sets."""
        q = lambda n: quote_identifier(n, active_dialect)
        columns = ", ".join([q(col['name']) for col in schema])
        num_cols = len(schema)
        
        # Build placeholders for multiple rows: (?, ?, ?), (?, ?, ?), ...
        single_row_placeholders = "(" + ", ".join(["?" for _ in range(num_cols)]) + ")"
        all_placeholders = ", ".join([single_row_placeholders for _ in range(num_rows)])
        
        return f"INSERT INTO {q(schema_name)}.{q(table_name)} ({columns}) VALUES {all_placeholders}"
    
    def initialise_connection() -> Tuple[pyodbc.Connection, pyodbc.Cursor, SqlDialect]:
        connection = get_db_connection(conn_str)
        active_dialect = get_sql_dialect(connection)
        cur = connection.cursor()
        return connection, cur, active_dialect
    
    def is_transient_connection_error(exc: Exception) -> bool:
        if isinstance(exc, pyodbc.Error):
            sqlstate = exc.args[0] if exc.args else ""
            if isinstance(sqlstate, str) and sqlstate.startswith("08"):
                return True
        return False
    
    conn: Optional[pyodbc.Connection] = None
    cursor: Optional[pyodbc.Cursor] = None
    active_dialect = dialect
    
    try:
        conn, cursor, active_dialect = initialise_connection()
        
        rows_inserted = 0
        batch_params: List[List] = []
        batch_meta: List[Tuple[int, Any]] = []
        
        def reconnect() -> None:
            nonlocal conn, cursor, active_dialect
            if cursor is not None:
                try:
                    cursor.close()
                except Exception:
                    pass
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass
            conn, cursor, active_dialect = initialise_connection()
        
        def flush_batch(params_batch: List[List], meta_batch: List[Tuple[int, Any]]):
            nonlocal rows_inserted
            if not params_batch:
                return
            
            attempts = 0
            num_rows_in_batch = len(params_batch)
            
            # Build INSERT statement for this specific batch size
            insert_sql = build_multi_row_insert_sql(active_dialect, num_rows_in_batch)
            
            # Flatten the batch parameters into a single list
            # e.g., [[1, 'a'], [2, 'b']] -> [1, 'a', 2, 'b']
            flat_params = [param for row in params_batch for param in row]
            
            # Debug logging for first batch
            if rows_inserted == 0:
                num_cols = len(schema)
                expected_params = num_rows_in_batch * num_cols
                logging.debug(f"First batch: {num_rows_in_batch} rows, {num_cols} cols/row, "
                            f"expected {expected_params} params, got {len(flat_params)} params")
            
            while True:
                try:
                    if cursor is None or conn is None:
                        raise RuntimeError("Database connection is not available during batch flush")
                    
                    cursor.execute(insert_sql, flat_params)
                    conn.commit()
                    rows_inserted += num_rows_in_batch
                    
                    if rows_inserted % adjusted_batch_size == 0 or rows_inserted == total_rows:
                        logging.info(f"Saved {rows_inserted} of {total_rows} rows...")
                    return
                except Exception as exc:
                    if conn is not None:
                        conn.rollback()
                    first_meta = meta_batch[0] if meta_batch else (None, None)
                    if is_transient_connection_error(exc) and attempts < max_reconnect_attempts:
                        attempts += 1
                        logging.warning(
                            "Connection issue while inserting batch starting at row index %s (Id=%s). "
                            "Attempting reconnect %d/%d...",
                            first_meta[0],
                            first_meta[1],
                            attempts,
                            max_reconnect_attempts,
                        )
                        reconnect()
                        continue
                    
                    logging.error(
                        "Fatal error inserting batch starting at row index %s (Id=%s): %s",
                        first_meta[0],
                        first_meta[1],
                        exc,
                    )
                    raise RuntimeError(
                        f"Failed to insert batch starting at row index {first_meta[0]} (Id={first_meta[1]})."
                    ) from exc
        
        for offset, (_, row) in enumerate(df.iterrows()):
            params = prepare_row(row, schema)
            batch_params.append(params)
            batch_meta.append((offset, row.get("Id")))
            if len(batch_params) >= adjusted_batch_size:
                flush_batch(batch_params, batch_meta)
                batch_params = []
                batch_meta = []
        if batch_params:
            flush_batch(batch_params, batch_meta)
        
        return rows_inserted
    finally:
        if cursor is not None:
            try:
                cursor.close()
            except Exception:
                pass
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


def insert_data_into_table(
    conn_str: str,
    schema_name: str,
    table_name: str,
    df: pd.DataFrame,
    schema: List[Dict],
    dialect: SqlDialect,
    batch_size: int = DEFAULT_INSERT_BATCH_SIZE,
    max_reconnect_attempts: int = MAX_RECONNECT_ATTEMPTS,
) -> int:
    """
    Insert data into a SQL Server or PostgreSQL table using batched executemany writes.

    The function owns all database connections, allowing it to reconnect and retry a batch when a
    transient connection error is detected. Returns the total number of rows inserted.
    """

    total_rows = len(df)
    if total_rows == 0:
        logging.info(f"No rows to insert into table '{table_name}'.")
        return 0

    def build_insert_sql(active_dialect: SqlDialect) -> str:
        q = lambda n: quote_identifier(n, active_dialect)
        columns = ", ".join([q(col['name']) for col in schema])
        placeholders = ", ".join(["?" for _ in schema])
        return f"INSERT INTO {q(schema_name)}.{q(table_name)} ({columns}) VALUES ({placeholders})"

    def initialise_connection() -> Tuple[pyodbc.Connection, pyodbc.Cursor, SqlDialect, str]:
        connection = get_db_connection(conn_str)
        active_dialect = get_sql_dialect(connection)
        insert_statement = build_insert_sql(active_dialect)
        cur = connection.cursor()
        if active_dialect.is_sql_server and hasattr(cur, "fast_executemany"):
            cur.fast_executemany = True
        return connection, cur, active_dialect, insert_statement

    def is_transient_connection_error(exc: Exception) -> bool:
        if isinstance(exc, pyodbc.Error):
            sqlstate = exc.args[0] if exc.args else ""
            if isinstance(sqlstate, str) and sqlstate.startswith("08"):
                return True
        return False

    conn: Optional[pyodbc.Connection] = None
    cursor: Optional[pyodbc.Cursor] = None
    active_dialect = dialect
    insert_sql = ""

    try:
        conn, cursor, active_dialect, insert_sql = initialise_connection()

        rows_inserted = 0
        batch_params: List[List] = []
        batch_meta: List[Tuple[int, Any]] = []

        def reconnect() -> None:
            nonlocal conn, cursor, active_dialect, insert_sql
            if cursor is not None:
                try:
                    cursor.close()
                except Exception:
                    pass
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass
            conn, cursor, active_dialect, insert_sql = initialise_connection()

        def flush_batch(params_batch: List[List], meta_batch: List[Tuple[int, Any]]):
            nonlocal rows_inserted
            if not params_batch:
                return

            attempts = 0
            while True:
                try:
                    if cursor is None or conn is None:
                        raise RuntimeError("Database connection is not available during batch flush")
                    cursor.executemany(insert_sql, params_batch)
                    conn.commit()
                    rows_inserted += len(params_batch)
                    if rows_inserted % batch_size == 0 or rows_inserted == total_rows:
                        logging.info(f"Saved {rows_inserted} of {total_rows} rows...")
                    return
                except Exception as exc:
                    if conn is not None:
                        conn.rollback()
                    first_meta = meta_batch[0] if meta_batch else (None, None)
                    if is_transient_connection_error(exc) and attempts < max_reconnect_attempts:
                        attempts += 1
                        logging.warning(
                            "Connection issue while inserting batch starting at row index %s (Id=%s). "
                            "Attempting reconnect %d/%d...",
                            first_meta[0],
                            first_meta[1],
                            attempts,
                            max_reconnect_attempts,
                        )
                        reconnect()
                        continue

                    logging.error(
                        "Fatal error inserting batch starting at row index %s (Id=%s): %s",
                        first_meta[0],
                        first_meta[1],
                        exc,
                    )
                    raise RuntimeError(
                        f"Failed to insert batch starting at row index {first_meta[0]} (Id={first_meta[1]})."
                    ) from exc

        for offset, (_, row) in enumerate(df.iterrows()):
            params = prepare_row(row, schema)
            batch_params.append(params)
            batch_meta.append((offset, row.get("Id")))
            if len(batch_params) >= batch_size:
                flush_batch(batch_params, batch_meta)
                batch_params = []
                batch_meta = []
        if batch_params:
            flush_batch(batch_params, batch_meta)

        return rows_inserted
    finally:
        if cursor is not None:
            try:
                cursor.close()
            except Exception:
                pass
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


def remove_empty_columns(df: pd.DataFrame) -> pd.DataFrame:
    """
    Remove columns from the DataFrame that are completely empty (all values are NaN/None).
    """
    empty_columns = [col for col in df.columns if df[col].isnull().all()]
    if empty_columns:
        logging.info(f"Removing empty columns: {empty_columns}")
    return df.drop(columns=empty_columns)

def convert_datetime_stored_as_text(df: pd.DataFrame) -> pd.DataFrame:
    """
    Convert columns with ISO 8601 date or timestamp strings to datetime64 dtype.
    """
    for col in df.columns:
        if df[col].dtype == 'O':  # Object type columns
            try:
                # Attempt to convert to datetime
                df[col] = pd.to_datetime(df[col], errors='raise', format="ISO8601")
                logging.info(f"Column '{col}' converted to datetime.")
            except (ValueError, TypeError):
                # If conversion fails, leave the column as is
                logging.debug(f"Column '{col}' is not a valid datetime format.")
    return df

def remove_rows_without_primarykey(df: pd.DataFrame, primary_key: str) -> pd.DataFrame:
    """
    Remove rows where the primary key column is empty (null or "").
    """
    initial_row_count = len(df)
    df = df[df[primary_key].notnull() & (df[primary_key] != "")]
    removed_rows = initial_row_count - len(df)
    if removed_rows > 0:
        logging.warning(f"Removed {removed_rows} rows where the primary key '{primary_key}' was empty.")
    return df

def save_to_database_table(
    conn_str: str,
    schema_name: str,
    table_name: str,
    df: pd.DataFrame,
    primary_key: str,
    remove_empty_primary_keys: bool = False,
    database_role: Optional[str] = None,
):
    """Save a DataFrame to a SQL Server or PostgreSQL table using resilient batching.
    
    Args:
        conn_str: Database connection string
        schema_name: Target schema name
        table_name: Target table name
        df: DataFrame to save
        primary_key: Column name to use as primary key (will be renamed to 'Id')
        remove_empty_primary_keys: If True, remove rows with empty primary key values
        database_role: Optional PostgreSQL role to SET before DDL operations (e.g., 'db_sleetmigration_owner')
    """

    total_start = perf_counter()
    if primary_key not in df.columns:
        raise ValueError(f"Primary key column '{primary_key}' not found in DataFrame.")

    prep_start = perf_counter()
    df = df.loc[:, ~df.columns.str.startswith('$')]
    df = remove_empty_columns(df)
    df = convert_datetime_stored_as_text(df)
    df = df.rename(columns={primary_key: 'Id'})
    if remove_empty_primary_keys:
        df = remove_rows_without_primarykey(df, 'Id')
    df = df[['Id'] + [col for col in df.columns if col != 'Id']]
    prep_time = perf_counter() - prep_start

    schema_start = perf_counter()
    schema_time = 0.0
    ddl_time = 0.0
    ddl_conn: Optional[pyodbc.Connection] = None
    ddl_cursor: Optional[pyodbc.Cursor] = None
    dialect: Optional[SqlDialect] = None
    schema: List[Dict] = []
    try:
        ddl_conn = get_db_connection(conn_str)
        dialect = get_sql_dialect(ddl_conn)
        schema = analyze_schema(df, dialect)
        schema_time = perf_counter() - schema_start

        ddl_start = perf_counter()
        create_table_sql = generate_create_table_sql(schema_name, table_name, schema, 'Id', dialect)
        ddl_cursor = ddl_conn.cursor()
        
        # Set role if specified (for PostgreSQL projects that require elevated permissions)
        if database_role and dialect.is_postgresql:
            logging.debug(f"Setting role to: {database_role}")
            ddl_cursor.execute(f"SET ROLE {database_role};")
        
        ddl_cursor.execute(create_table_sql)
        ddl_conn.commit()
        logging.info(f"Table '{table_name}' created successfully.")
        ddl_time = perf_counter() - ddl_start
    finally:
        if ddl_cursor is not None:
            try:
                ddl_cursor.close()
            except Exception:
                pass
        if ddl_conn is not None:
            try:
                ddl_conn.close()
            except Exception:
                pass

    if dialect is None:
        raise RuntimeError("Failed to determine SQL dialect for connection string")

    insert_start = perf_counter()
    rows_inserted = insert_data_into_table(
        conn_str,
        schema_name,
        table_name,
        df,
        schema,
        dialect,
        batch_size=DEFAULT_INSERT_BATCH_SIZE,
    )
    ## TODO - implement switch to batch_insert_data_into_table for PostgreSQL
    # rows_inserted = batch_insert_data_into_table(
    #     conn_str,
    #     schema_name,
    #     table_name,
    #     df,
    #     schema,
    #     dialect,
    #     batch_size=DEFAULT_INSERT_BATCH_SIZE,
    # )
    insert_time = perf_counter() - insert_start
    total_time = perf_counter() - total_start

    logging.info(
        "Successfully inserted %d rows into table '%s' (batchSize=%d, method=batch_insert). Timing: prep=%.2fs schema=%.2fs ddl=%.2fs insert=%.2fs total=%.2fs",
        rows_inserted,
        table_name,
        DEFAULT_INSERT_BATCH_SIZE,
        prep_time,
        schema_time,
        ddl_time,
        insert_time,
        total_time,
    )


def get_value(value, expected_type, default=None, decimals=None, clean=False):
    """
    Utility function to get a value or default with type checking, rounding, and optional cleaning.

    Parameters:
    - value: The input value to process.
    - expected_type: The expected type of the value (e.g., int, float, str, bool).
    - default: The default value to return if the input value is None or empty.
    - decimals: Number of decimal places to round to (applicable for float values).
    - clean: Whether to clean the value by removing control characters (applicable for strings).

    Returns:
    - Processed value of the expected type or the default value.
    """
    if value is None or value == '':
        return default

    if clean and isinstance(value, str):
        # 1. Remove control characters (keep only printable, >= space)
        cleaned = ''.join(ch for ch in value.strip() if ord(ch) >= 32)

        # 2. Remove / filter out emoji and "extreme" unicode not expected in European names.
        #    Strategy:
        #    - Exclude characters outside the Basic Multilingual Plane (code points > 0xFFFF) – mostly emoji/symbols.
        #    - Exclude characters whose Unicode category starts with 'C' (Other: control, surrogate, private-use).
        #    - Exclude Symbol categories 'So' (Other Symbol) and 'Sk' (Modifier Symbol) which cover most pictographs / emoji variants.
        #    - Explicitly drop zero-width joiners and variation selectors used in emoji composition.
        #    - Keep standard letters (including extended Latin), marks (for accents), numbers, basic punctuation (space, - ' .), and commas if present.
        #    This should retain names like "Åsa-Léa O'Connor" while stripping "👩‍🔧🚗" and similar.

        disallowed_single_chars = {"\u200D", "\uFE0F"}  # zero-width joiner, variation selector 16
        allowed_basic_punct = {"-", "'", " ", ".", ","}

        def _is_allowed(ch: str) -> bool:
            cp = ord(ch)
            if ch in disallowed_single_chars:
                return False
            if cp > 0xFFFF:  # Outside BMP – treat as emoji/symbol for this business rule
                return False
            cat = unicodedata.category(ch)
            if cat.startswith('C'):  # Control / surrogate / private use / unassigned
                return False
            if cat in ('So', 'Sk'):  # Other Symbol / Modifier Symbol (most emoji & decorative symbols)
                return False
            # Allow letters, marks (accents), numbers
            if cat[0] in ('L', 'M', 'N'):
                return True
            # Allow limited punctuation useful in names
            if ch in allowed_basic_punct:
                return True
            return False

        cleaned = ''.join(ch for ch in cleaned if _is_allowed(ch))

        # 3. Collapse multiple spaces
        cleaned = re.sub(r'\s{2,}', ' ', cleaned).strip()

        # If string becomes empty after sanitization, fall back to default
        if cleaned == '':
            return default
        value = cleaned

    if expected_type == bool:
        if isinstance(value, str):
            # Handle string representations of booleans from different databases
            if value.lower() in ('0', 'false', 'f', 'n', 'no', 'off'):
                return False
            elif value.lower() in ('1', 'true', 't', 'y', 'yes', 'on'):
                return True
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

def try_get_value(row, column_name, expected_type, default=None, decimals=None, clean=False):
    """
    Safely get a value from a row by column name. If the column does not exist or the value is None/empty, return the default.
    """
    try:
        value = getattr(row, column_name, None)
        return get_value(value, expected_type, default=default, decimals=decimals, clean=clean)
    except AttributeError:
        return default


