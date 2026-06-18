import logging
import os
import pandas as pd
import httpx
from dotenv import load_dotenv
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

from utils.config_utils import get_db_connection_string
from utils.dbutils import save_to_database_table
from utils.log_utils import setup_logging

load_dotenv()

SCHEMA_NAME = "Source"
TABLE_NAME = "TeltonikaDevices"
PAGE_SIZE = 100
PROGRESS_LOG_INTERVAL = 500


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=30),
    retry=retry_if_exception_type((httpx.HTTPStatusError, httpx.RequestError)),
    before_sleep=lambda retry_state: logging.warning(
        f"Retrying RMS API call (attempt {retry_state.attempt_number})..."
    ),
)
def _fetch_page(client: httpx.Client, offset: int) -> dict:
    """Fetch a single page of devices from the RMS API."""
    resp = client.get("/api/devices", params={"limit": PAGE_SIZE, "offset": offset})
    resp.raise_for_status()
    return resp.json()


def fetch_all_devices() -> list[dict]:
    """Fetch all devices from Teltonika RMS API with pagination."""
    api_url = os.getenv("RMS_API_URL")
    api_token = os.getenv("RMS_API_TOKEN")

    if not api_url or not api_token:
        raise ValueError("RMS_API_URL and RMS_API_TOKEN must be set in .env")

    client = httpx.Client(
        base_url=api_url.rstrip("/"),
        headers={
            "Accept": "application/json",
            "Authorization": f"Bearer {api_token}",
        },
        timeout=30.0,
    )

    devices: list[dict] = []
    offset = 0

    try:
        while True:
            body = _fetch_page(client, offset)
            page_data = body["data"]
            total = body["meta"]["total"]

            devices.extend(page_data)

            if len(devices) >= total or len(page_data) < PAGE_SIZE:
                break

            offset += PAGE_SIZE

            if len(devices) % PROGRESS_LOG_INTERVAL < PAGE_SIZE:
                logging.info(f"  Fetched {len(devices)}/{total} devices...")
    finally:
        client.close()

    logging.info(f"Fetched {len(devices)} devices from Teltonika RMS (total reported: {total})")
    return devices


def main():
    setup_logging("FetchFromTeltonika")
    logging.info("=" * 60)
    logging.info("Fetching devices from Teltonika RMS")
    logging.info("=" * 60)

    conn_str = get_db_connection_string()
    if not conn_str:
        logging.error("Database connection string is not configured")
        raise SystemExit(1)

    devices = fetch_all_devices()

    if not devices:
        logging.warning("No devices returned from Teltonika RMS API")
        return

    df = pd.json_normalize(devices)
    logging.info(f"Normalized {len(df)} devices with {len(df.columns)} columns")

    database_role = os.getenv("AMPECO_DATABASE_ROLE")
    save_to_database_table(
        conn_str=conn_str,
        schema_name=SCHEMA_NAME,
        table_name=TABLE_NAME,
        df=df,
        primary_key="id",
        database_role=database_role,
    )
    logging.info(f"✓ Saved {len(df)} devices to {SCHEMA_NAME}.{TABLE_NAME}")


if __name__ == "__main__":
    main()
