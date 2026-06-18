import requests
import json
import logging
from utils.config_utils import get_db_connection_string, get_api_base_url, get_api_token
from utils.log_utils import setup_logging
from utils.ampeco_utils import execute_ampeco_api_call, check_ampeco_response
from utils.dbutils import get_value, try_get_value, get_db_connection, get_sql_dialect, quote_identifier
from utils.locale_utils import normalize_locale

# Constants
DECIMALS_FOR_PRICES = 2

# Initialize counters and error list
total_rows = 0
created_count = 0
updated_count = 0
simple_tariff_created_count = 0
simple_tariff_updated_count = 0
error_count = 0
errors = []
warnings = []

def fetch_tariff_groups(conn_str):
    """Fetch all Tariff Groups and Base Tariff (dialect-aware), filter TargetPartnerID IS NOT NULL."""
    with get_db_connection(conn_str) as conn:
        cursor = conn.cursor()
        dialect = get_sql_dialect(conn)
        target_schema = quote_identifier("Target", dialect)
        view_name = quote_identifier("TariffGroupsAndBaseTariff", dialect)
        target_partner_id = quote_identifier("TargetPartnerID", dialect)
        query = f"SELECT * FROM {target_schema}.{view_name} WHERE {target_partner_id} IS NOT NULL"
        cursor.execute(query)
        return cursor.fetchall()

def fetch_simple_tariffs(conn_str):
    """Fetch all simple tariffs (dialect-aware), filter TargetTariffGroupID IS NOT NULL.

    If the view/table doesn't exist, log an error and return an empty list.
    """
    try:
        with get_db_connection(conn_str) as conn:
            cursor = conn.cursor()
            dialect = get_sql_dialect(conn)
            target_schema = quote_identifier("Target", dialect)
            view_name = quote_identifier("Tariff_Simple", dialect)
            target_group_id = quote_identifier("TargetTariffGroupID", dialect)
            query = f"SELECT * FROM {target_schema}.{view_name} WHERE {target_group_id} IS NOT NULL"
            cursor.execute(query)
            return cursor.fetchall()
    except Exception as ex:
        logging.error(f"Failed to fetch simple tariffs (continuing with empty): {ex}")
        return []

def create_tariff_group(tariff_group_data):
    """Create a tariff group in Ampeco API."""
    def api_call(base_url, headers):
        url = f"{base_url}/resources/tariff-groups/v1.0"
        return requests.post(url, headers=headers, json=tariff_group_data)

    response = execute_ampeco_api_call(api_call)
    return check_ampeco_response(response, 201, 'create_tariff_group')

def update_tariff_group(tariff_group_id, tariff_group_data):
    """Update a tariff group in Ampeco API."""
    def api_call(base_url, headers):
        url = f"{base_url}/resources/tariff-groups/v1.0/{tariff_group_id}"
        return requests.put(url, headers=headers, json=tariff_group_data)

    response = execute_ampeco_api_call(api_call)
    return check_ampeco_response(response, 200, 'update_tariff_group')

def create_tariff(tariff_data):
    """Create a tariff in Ampeco API."""
    def api_call(base_url, headers):
        url = f"{base_url}/resources/tariffs/v1.0"
        return requests.post(url, headers=headers, json=tariff_data)

    response = execute_ampeco_api_call(api_call)
    return check_ampeco_response(response, 201, 'create_tariff')

def update_tariff(tariff_id, tariff_data):
    """Update a tariff in Ampeco API."""
    def api_call(base_url, headers):
        url = f"{base_url}/resources/tariffs/v1.0/{tariff_id}"
        return requests.put(url, headers=headers, json=tariff_data)

    response = execute_ampeco_api_call(api_call)
    return check_ampeco_response(response, 200, 'update_tariff')

def get_tariff_group(tariff_group_id):
    """Get a tariff group from Ampeco API."""
    def api_call(base_url, headers):
        url = f"{base_url}/resources/tariff-groups/v1.0/{tariff_group_id}"
        return requests.get(url, headers=headers)

    response = execute_ampeco_api_call(api_call)
    return check_ampeco_response(response, 200, 'get_tariff_group')

def get_tariff(tariff_id):
    """Get a tariff from Ampeco API."""
    def api_call(base_url, headers):
        url = f"{base_url}/resources/tariffs/v1.0/{tariff_id}"
        return requests.get(url, headers=headers)

    response = execute_ampeco_api_call(api_call)
    return check_ampeco_response(response, 200, 'get_tariff')

def get_tariff_restriction_tier(restrictions):
    """Determine the restriction tier for a tariff based on its restrictions.

    Tiers (lower = earlier in list, evaluated last by CSMS):
        0: Base tariff (no restrictions, fallback)
        1: No restrictions
        2: AdHoc or Partner restriction (applyToAdHocUsers=true OR applyToUsersOfChargePointPartner=true)
        3: User Group restriction (applyToUserGroupIds is not empty)

    Args:
        restrictions: Dict with restriction fields, or None for base tariff

    Returns:
        int: Tier number (0-3)
    """
    if restrictions is None:
        return 0  # Base tariff

    # Check for user group restriction (most specific, should be last)
    user_group_ids = restrictions.get('applyToUserGroupIds', [])
    if user_group_ids and len(user_group_ids) > 0:
        return 3

    # Check for AdHoc, Partner, or Roaming restriction
    if restrictions.get('applyToAdHocUsers', False) or restrictions.get('applyToUsersOfChargePointPartner', False) or restrictions.get('applyToUsersOfAllRoamingEmsps', False):
        return 2

    # No restrictions
    return 1

def get_tariff_restriction_tier_from_row(row):
    """Determine the restriction tier for a tariff from a database row.

    Args:
        row: Database row with restriction columns

    Returns:
        int: Tier number (1-3, base tariff uses tier 0 but is handled separately)
    """
    # Check for user group restriction
    user_group_id = getattr(row, 'restrictions_applyToUserGroupIds', None)
    if user_group_id:
        return 3

    # Check for AdHoc, Partner, or Roaming restriction
    is_adhoc = getattr(row, 'restrictions_applyToAdHocUsers', False)
    is_partner = getattr(row, 'restrictions_applyToUsersOfChargePointPartner', False)
    is_roaming = getattr(row, 'restrictions_applyToUsersOfAllRoamingEmsps', False)
    if is_adhoc or is_partner or is_roaming:
        return 2

    # No restrictions
    return 1

def insert_or_update_mapping_for_tariff_group(conn_str, row, tariff_group_id, tariff_base_id):
    """Single-query mapping update for tariff group/base with multi-source support.

    Precedence:
        - If SourceLocationID present (Charge365/PostgreSQL), UPDATE "Mapping"."LocationMapping"
            setting both "TargetTariffGroupID" and "TargetTariffBaseID" using "SourceLocationID" as the key.
    - Elif SourceStationID present (Current/MSSQL), update [Mapping].[StationMapping].
    - Elif SourceOrganizationID & SourceTariffID present (EV-Advisor/MSSQL), update [Mapping].[TariffMapping] or insert if absent.
    """
    with get_db_connection(conn_str) as conn:
        cursor = conn.cursor()

        query = None
        params = None
        success_msg = None

        # Generic mapping_table/mapping_key pattern (key-based migrations like Project Sleet)
        if hasattr(row, "mapping_table") and row.mapping_table:
            table_name = row.mapping_table  # e.g., "tariff_group_mapping"
            query = f'''
                INSERT INTO "Mapping"."{table_name}" (mapping_key, target_tariff_group_id, target_tariff_base_id)
                VALUES (?, ?, ?)
                ON CONFLICT (mapping_key) DO UPDATE SET
                    target_tariff_group_id = EXCLUDED.target_tariff_group_id,
                    target_tariff_base_id = EXCLUDED.target_tariff_base_id
            '''
            params = (row.mapping_key, tariff_group_id, tariff_base_id)
            success_msg = f"Upserted {table_name} for mapping_key: {row.mapping_key} with tariff_group_id={tariff_group_id}, tariff_base_id={tariff_base_id}"

        elif hasattr(row, "SourceLocationID") and getattr(row, "SourceLocationID") is not None:
            # Charge365/PostgreSQL path: update LocationMapping by SourceLocationID, setting both Target IDs
            query = (
                """
                UPDATE "Mapping"."LocationMapping"
                SET "TargetTariffGroupID" = ?,
                    "TargetTariffBaseID" = ?
                WHERE "SourceLocationID" = ?
                """
            )
            params = (tariff_group_id, tariff_base_id, getattr(row, "SourceLocationID"))
            success_msg = "Updated LocationMapping with TariffGroup/Base"

        elif hasattr(row, "SourceStationID"):
            query = (
                """
                UPDATE [Mapping].[StationMapping]
                SET TargetTariffGroupID = ?, TargetTariffBaseID = ?
                WHERE SourceStationID = ?
                """
            )
            params = (tariff_group_id, tariff_base_id, getattr(row, "SourceStationID"))
            success_msg = "Updated StationMapping with TariffGroup/Base"

        elif hasattr(row, "SourceOrganizationID") and hasattr(row, "SourceTariffID"):
            # We'll try update first; if 0, we'll insert in a second statement respecting the single-query+check philosophy per branch
            cursor.execute(
                """
                UPDATE [Mapping].[TariffMapping]
                SET TargetTariffGroupID = ?, TargetTariffBaseID = ?
                WHERE SourceOrganizationID = ? AND SourceTariffID = ?
                """,
                (tariff_group_id, tariff_base_id, getattr(row, "SourceOrganizationID"), getattr(row, "SourceTariffID"))
            )
            if cursor.rowcount == 0:
                cursor.execute(
                    """
                    INSERT INTO [Mapping].[TariffMapping] (SourceOrganizationID, SourceTariffID, TargetTariffGroupID, TargetTariffBaseID)
                    VALUES (?, ?, ?, ?)
                    """,
                    (getattr(row, "SourceOrganizationID"), getattr(row, "SourceTariffID"), tariff_group_id, tariff_base_id)
                )
                if cursor.rowcount == 0:
                    raise SystemExit("TariffMapping insert affected 0 rows; halting.")
                conn.commit()
                logging.info("Inserted TariffMapping with TariffGroup/Base")
                return
            else:
                conn.commit()
                logging.info("Updated TariffMapping with TariffGroup/Base")
                return

        if query is None or params is None:
            raise SystemExit("No valid mapping condition found; query not built. Halting execution.")

        cursor.execute(query, params)
        if cursor.rowcount == 0:
            raise SystemExit("Mapping change affected 0 rows; halting execution to prevent missing mapping.")
        conn.commit()
        logging.info(success_msg)

def insert_or_update_mapping_for_tariff(conn_str, row, target_tariff_id):
    """Single-query mapping for simple tariffs with multi-source support.

    - If SourcePriceGroupID present (Charge365/PostgreSQL), upsert into "Mapping"."PriceGroupMapping" setting TargetTariffID.
    - Elif SourceChargingStationGroupServiceID present (MSSQL), insert into [Mapping].[ChargingStationGroupServiceMapping].
    """
    with get_db_connection(conn_str) as conn:
        cursor = conn.cursor()

        query = None
        params = None
        success_msg = None

        # Generic mapping_table/mapping_key pattern (key-based migrations like Project Sleet)
        if hasattr(row, "mapping_table") and row.mapping_table:
            table_name = row.mapping_table  # e.g., "tariff_mapping"
            query = f'''
                INSERT INTO "Mapping"."{table_name}" (mapping_key, target_tariff_id)
                VALUES (?, ?)
                ON CONFLICT (mapping_key) DO UPDATE SET target_tariff_id = EXCLUDED.target_tariff_id
            '''
            params = (row.mapping_key, target_tariff_id)
            success_msg = f"Upserted {table_name} target_tariff_id for mapping_key: {row.mapping_key}"

        elif hasattr(row, "SourcePriceGroupID") and getattr(row, "SourcePriceGroupID") is not None:
            tariff_type = getattr(row, "TargetTariffType", None)
            query = (
                """
                INSERT INTO "Mapping"."PriceGroupMapping"
                    ("SourcePriceGroupID", "TargetTariffType", "TargetTariffID")
                VALUES (?, ?, ?)
                ON CONFLICT ("SourcePriceGroupID","TargetTariffType") DO UPDATE
                SET "TargetTariffID" = EXCLUDED."TargetTariffID"
                """
            )
            params = (getattr(row, "SourcePriceGroupID"), tariff_type, target_tariff_id)
            success_msg = "Upserted PriceGroupMapping with TargetTariffType & TargetTariffID"

        elif hasattr(row, "SourceChargingStationGroupServiceID"):
            query = (
                """
                INSERT INTO [Mapping].[ChargingStationGroupServiceMapping] (SourceChargingStationGroupServiceID, TargetTariffID)
                VALUES (?, ?)
                """
            )
            params = (getattr(row, "SourceChargingStationGroupServiceID"), target_tariff_id)
            success_msg = "Inserted ChargingStationGroupServiceMapping"

        if query is None or params is None:
            raise SystemExit("No valid mapping condition found; query not built. Halting execution.")

        cursor.execute(query, params)
        if cursor.rowcount == 0:
            raise SystemExit("Mapping change affected 0 rows; halting execution to prevent missing mapping.")
        conn.commit()
        logging.info(success_msg)

def process_tariff_group(row, conn_str):
    """Processes each tariff group row."""
    global created_count, updated_count, error_count
    target_tariff_group_id = row.TargetTariffGroupID
    target_tariff_base_id = row.TargetTariffBaseID

    tariff_group_data = {
        "name": get_value(row.tariffGroup_name, str),
        "partnerId": get_value(row.tariffGroup_partnerId, int)
    }

    base_tariff_data = {
        "type": get_value(row.basetariff_type, str),
        "name": get_value(row.basetariff_name, str),
        "description": {},
        "additionalInformation": {},
        "pricing": {
            "connectionFee": get_value(row.basetariff_pricing_connectionFee, float, decimals=DECIMALS_FOR_PRICES),
            "pricePerKwh": get_value(row.basetariff_pricing_pricePerKwh, float, decimals=DECIMALS_FOR_PRICES),
            "pricePeriodInMinutes": get_value(row.basetariff_pricing_pricePeriodInMinutes, int),
            "pricePerPeriod": get_value(row.basetariff_pricing_pricePerPeriod, float, decimals=DECIMALS_FOR_PRICES),
            "idleFeePerMinute": get_value(row.basetariff_pricing_idleFeePerMinute, float, decimals=DECIMALS_FOR_PRICES),
            "idleFeeGracePeriodMinutes": get_value(row.basetariff_pricing_idleFeeGracePeriodMinutes, int),
            "connectionFeeMinimumSessionDuration": get_value(row.basetariff_pricing_connectionFeeMinimumSessionDuration, int),
            "connectionFeeMinimumSessionEnergy": get_value(row.basetariff_pricing_connectionFeeMinimumSessionEnergy, float, decimals=DECIMALS_FOR_PRICES),
            "durationFeeGracePeriod": get_value(row.basetariff_pricing_durationFeeGracePeriod, int),
            "minPrice": get_value(row.basetariff_pricing_minPrice, float, decimals=DECIMALS_FOR_PRICES),
            "preAuthorizeAmount": get_value(row.basetariff_pricing_preAuthorizeAmount, float, decimals=DECIMALS_FOR_PRICES),
            "taxID": get_value(row.basetariff_pricing_taxID, int)
        },
        "partner": {"id": get_value(row.basetariff_partner_id, int)}
    }

    # If idleFeePerMinute is null, remove both idleFeePerMinute and idleFeeGracePeriodMinutes
    if base_tariff_data["pricing"].get("idleFeePerMinute") is None:
        base_tariff_data["pricing"].pop("idleFeePerMinute", None)
        base_tariff_data["pricing"].pop("idleFeeGracePeriodMinutes", None)

    # Dynamically add translations for description and additionalInformation
    for column_name in row.cursor_description:
        col = column_name[0]
        if col.startswith("basetariff_description_"):
            locale = normalize_locale(col.split("_", 2)[2])
            base_tariff_data["description"][locale] = get_value(getattr(row, col), str, default="<div></div>")
        elif col.startswith("basetariff_additionalInformation_"):
            locale = normalize_locale(col.split("_", 2)[2])
            base_tariff_data["additionalInformation"][locale] = get_value(getattr(row, col), str, default="<div></div>")

    # Special handling for tariffs of type 'free' and 'charging not allowed':
    if base_tariff_data["type"] in ["free", "charging not allowed"]:
        del base_tariff_data["pricing"]

    try:
        if target_tariff_group_id is None:
            # Create a new TariffGroup
            response_data = create_tariff_group(tariff_group_data)
            target_tariff_group_id = response_data['id']
            target_tariff_base_id = response_data['tariffIds'][0]

            # Update the mapping table
            insert_or_update_mapping_for_tariff_group(conn_str, row, target_tariff_group_id, target_tariff_base_id)
            created_count += 1
            logging.info(f"  Created tariff group {target_tariff_group_id} '{tariff_group_data['name']}' with base tariff {target_tariff_base_id}")
        else:
            # Get the existing TariffGroup
            existing_tariff_group = get_tariff_group(target_tariff_group_id)
            # Update the existing data with new data
            existing_tariff_group.update(tariff_group_data)
            # Update the existing TariffGroup
            update_tariff_group(target_tariff_group_id, existing_tariff_group)
            updated_count += 1
            logging.info(f"  Updated tariff group {target_tariff_group_id} '{tariff_group_data['name']}'")

        # Update the base tariff
        update_tariff(target_tariff_base_id, base_tariff_data)
        logging.info(f"  Updated base tariff {target_tariff_base_id} '{base_tariff_data['name']}'")
    except Exception as e:
        error_count += 1
        errors.append((row, str(e)))
        logging.error(f"Error processing row {row}: {e}")

def process_simple_tariffs(simple_tariffs, conn_str):
    """Process all simple tariffs."""
    global simple_tariff_created_count, simple_tariff_updated_count, error_count
    for index, row in enumerate(simple_tariffs, start=1):
        logging.info(f"Processing simple tariff '{row.name}' ({index} of {len(simple_tariffs)})...")
        tariff_data = {
            "type": get_value(row.type, str),
            "name": get_value(row.name, str),
            "description": {},
            "additionalInformation": {},
            "pricing": {
                "connectionFee": get_value(row.pricing_connectionFee, float, decimals=DECIMALS_FOR_PRICES),
                "pricePerKwh": get_value(row.pricing_pricePerKwh, float, decimals=DECIMALS_FOR_PRICES),
                "pricePeriodInMinutes": get_value(row.pricing_pricePeriodInMinutes, int),
                "pricePerPeriod": get_value(row.pricing_pricePerPeriod, float, decimals=DECIMALS_FOR_PRICES),
                "idleFeePerMinute": get_value(row.pricing_idleFeePerMinute, float, decimals=DECIMALS_FOR_PRICES),
                "idleFeeGracePeriodMinutes": get_value(row.pricing_idleFeeGracePeriodMinutes, int),
                "connectionFeeMinimumSessionDuration": get_value(row.pricing_connectionFeeMinimumSessionDuration, int),
                "connectionFeeMinimumSessionEnergy": get_value(row.pricing_connectionFeeMinimumSessionEnergy, float, decimals=DECIMALS_FOR_PRICES),
                "durationFeeGracePeriod": get_value(row.pricing_durationFeeGracePeriod, int),
                "minPrice": get_value(row.pricing_minPrice, float, decimals=DECIMALS_FOR_PRICES),
                "preAuthorizeAmount": get_value(row.pricing_preAuthorizeAmount, float, decimals=DECIMALS_FOR_PRICES),
                "taxID": get_value(row.pricing_taxID, int),
                "chargePointElectricityRate": try_get_value(row, "pricing_chargePointElectricityRate", bool),
                "fallbackElectricityRateId": try_get_value(row, "pricing_fallbackElectricityRateId", int),
                "markupFixedFeePerKwh": try_get_value(row, "pricing_markupFixedFeePerKwh", float, decimals=DECIMALS_FOR_PRICES),
                "markupPercentagePerKwh": try_get_value(row, "pricing_markupPercentagePerKwh", float, decimals=DECIMALS_FOR_PRICES)
            },
            "partner": {"id": get_value(row.partner_id, int)},
            "restrictions": {
                "applyToUserGroupIds": [get_value(row.restrictions_applyToUserGroupIds, int)],
                "applyToUsersOfChargePointPartner": get_value(row.restrictions_applyToUsersOfChargePointPartner, bool),
                "applyToUsersOfAllRoamingEmsps": try_get_value(row, "restrictions_applyToUsersOfAllRoamingEmsps", bool),
                # Ad-Hoc user related fields (always added first, may be removed conditionally below)
                "applyToAdHocUsers": get_value(row.restrictions_applyToAdHocUsers, bool),
                "adHocPreAuthorizeAmount": get_value(row.restrictions_adHocPreAuthorizeAmount, float, decimals=DECIMALS_FOR_PRICES),
                "adHocStopWhenPreAuthorizedAmountFallsBelow": get_value(row.restrictions_adHocStopWhenPreAuthorizedAmountFallsBelow, float, decimals=DECIMALS_FOR_PRICES)
            }
        }

        # If idleFeePerMinute is null, remove both idleFeePerMinute and idleFeeGracePeriodMinutes
        if tariff_data["pricing"].get("idleFeePerMinute") is None:
            tariff_data["pricing"].pop("idleFeePerMinute", None)
            tariff_data["pricing"].pop("idleFeeGracePeriodMinutes", None)

        # Remove any remaining None values from the pricing block (API rejects null for numeric fields)
        tariff_data["pricing"] = {k: v for k, v in tariff_data["pricing"].items() if v is not None}

        # Remove None values from restrictions (optional columns may be absent in some projects)
        tariff_data["restrictions"] = {k: v for k, v in tariff_data["restrictions"].items() if v is not None}

        # Dynamically add translations for description and additionalInformation
        for column_name in row.cursor_description:
            col = column_name[0]
            if col.startswith("description_"):
                locale = col.split("_", 1)[1]
                tariff_data["description"][locale] = get_value(getattr(row, col), str, default="<div></div>")
            elif col.startswith("additionalInformation_"):
                locale = col.split("_", 1)[1]
                tariff_data["additionalInformation"][locale] = get_value(getattr(row, col), str, default="<div></div>")

        # Remove applyToUsersWithGroups if restrictions_applyToUserGroupIds is null or 0
        if not row.restrictions_applyToUserGroupIds:
            del tariff_data["restrictions"]["applyToUserGroupIds"]

        # Remove Ad-Hoc related amounts if applyToAdHocUsers is False or None
        if not tariff_data["restrictions"].get("applyToAdHocUsers"):
            tariff_data["restrictions"].pop("adHocPreAuthorizeAmount", None)
            tariff_data["restrictions"].pop("adHocStopWhenPreAuthorizedAmountFallsBelow", None)

        # Special handling for free tariffs
        if tariff_data["type"] == "free":
            del tariff_data["pricing"]

        try:
            if row.TargetTariffID is None:
                # Create a new Tariff
                response_data = create_tariff(tariff_data)
                target_tariff_id = response_data['id']

                # Insert into mapping table
                insert_or_update_mapping_for_tariff(conn_str, row, target_tariff_id)
                simple_tariff_created_count += 1
                logging.info(f"  Created tariff {target_tariff_id} '{row.name}' (group {row.TargetTariffGroupID})")
            else:
                # Update the existing Tariff
                update_tariff(row.TargetTariffID, tariff_data)
                simple_tariff_updated_count += 1
                logging.info(f"  Updated tariff {row.TargetTariffID} '{row.name}' (group {row.TargetTariffGroupID})")
        except Exception as e:
            error_count += 1
            errors.append((row, str(e)))
            logging.error(f"Error processing row {row}: {e}")

def update_tariff_groups_tariffs_ids(simple_tariffs):
    """Update tariff groups with the IDs of created simple tariffs.

    Ensures tariffs are ordered by restriction tier for correct CSMS evaluation:
        - Tier 0: Base tariff (first, fallback)
        - Tier 1: No restrictions
        - Tier 2: AdHoc or Partner restriction
        - Tier 3: User Group restriction (last, most specific)

    Relative order within each tier is preserved to respect manual operator adjustments.
    """
    global error_count, errors
    grouped_tariffs = {}
    for row in simple_tariffs:
        grouped_tariffs.setdefault(row.TargetTariffGroupID, []).append(row)

    for group_index, (group_id, tariffs) in enumerate(grouped_tariffs.items(), start=1):
        try:
            logging.info(f"Updating Tariff Group {group_id} ({group_index} of {len(grouped_tariffs)})...")
            current_tariff_group = get_tariff_group(group_id)
            current_tariff_ids = current_tariff_group['tariffIds']
            base_tariff_id = tariffs[0].TargetTariffBaseID

            # Build lookup from known simple tariffs: tariff_id -> row
            known_tariff_rows = {row.TargetTariffID: row for row in tariffs if row.TargetTariffID}
            simple_tariff_ids = list(known_tariff_rows.keys())

            # Add new simple tariffs to the list if not already present
            for tariff_id in simple_tariff_ids:
                if tariff_id not in current_tariff_ids:
                    current_tariff_ids.append(tariff_id)

            # Identify unknown tariffs and fetch their restrictions from API
            known_tariffs = [base_tariff_id] + simple_tariff_ids
            unknown_tariff_ids = [tid for tid in current_tariff_ids if tid not in known_tariffs]

            # Cache for unknown tariff restrictions (fetched from API)
            unknown_tariff_restrictions = {}
            if unknown_tariff_ids:
                warning_msg = f"Tariff Group {group_id} ({current_tariff_group['name']}) has unknown Tariffs: {unknown_tariff_ids}"
                logging.warning(warning_msg)
                warnings.append(warning_msg)
                for unknown_id in unknown_tariff_ids:
                    try:
                        tariff_data = get_tariff(unknown_id)
                        unknown_tariff_restrictions[unknown_id] = tariff_data.get('restrictions', {})
                        logging.info(f"  Fetched restrictions for unknown tariff {unknown_id}: tier {get_tariff_restriction_tier(unknown_tariff_restrictions[unknown_id])}")
                    except Exception as fetch_err:
                        logging.warning(f"  Could not fetch tariff {unknown_id}: {fetch_err}. Treating as tier 1 (no restrictions).")
                        unknown_tariff_restrictions[unknown_id] = {}

            # Build list of (tariff_id, tier, original_index) for sorting
            tariff_sort_data = []
            for original_index, tariff_id in enumerate(current_tariff_ids):
                if tariff_id == base_tariff_id:
                    tier = 0  # Base tariff
                elif tariff_id in known_tariff_rows:
                    tier = get_tariff_restriction_tier_from_row(known_tariff_rows[tariff_id])
                elif tariff_id in unknown_tariff_restrictions:
                    tier = get_tariff_restriction_tier(unknown_tariff_restrictions[tariff_id])
                else:
                    tier = 1  # Fallback: treat as no restrictions
                tariff_sort_data.append((tariff_id, tier, original_index))

            # Stable sort by tier, preserving original order within each tier
            tariff_sort_data.sort(key=lambda x: (x[1], x[2]))

            # Extract sorted tariff IDs
            sorted_tariff_ids = [item[0] for item in tariff_sort_data]

            # Log if order changed
            if sorted_tariff_ids != current_tariff_ids:
                logging.info(f"  Reordering tariffs in group {group_id}:")
                logging.info(f"    Before: {current_tariff_ids}")
                logging.info(f"    After:  {sorted_tariff_ids}")
                for tariff_id, tier, orig_idx in tariff_sort_data:
                    tariff_name = known_tariff_rows[tariff_id].name if tariff_id in known_tariff_rows else f"(unknown:{tariff_id})"
                    if tariff_id == base_tariff_id:
                        tariff_name = "(base tariff)"
                    logging.info(f"      Tier {tier}: {tariff_id} - {tariff_name}")

            # Update the tariff group with sorted tariff IDs
            current_tariff_group['tariffIds'] = sorted_tariff_ids
            update_tariff_group(group_id, current_tariff_group)
        except Exception as e:
            error_count += 1
            errors.append((group_id, str(e)))
            logging.error(f"Error updating tariff group {group_id}: {e}")

def main():
    """Main function to fetch and process tariff groups."""
    global total_rows
    conn_str = get_db_connection_string()
    try:
        tariff_groups = fetch_tariff_groups(conn_str)
        total_rows = len(tariff_groups)
        logging.info(f"Total tariff groups fetched: {total_rows}")

        for index, row in enumerate(tariff_groups, start=1):
            logging.info(f"Processing {row.tariffGroup_name} ({index} of {total_rows})...")
            process_tariff_group(row, conn_str)

        # Create and udpate tariffs
        simple_tariffs = fetch_simple_tariffs(conn_str)
        process_simple_tariffs(simple_tariffs, conn_str)

        # Refresh simple tariffs to get any new IDs and update tariff groups
        simple_tariffs = fetch_simple_tariffs(conn_str)
        update_tariff_groups_tariffs_ids(simple_tariffs)

        # Summarize results
        logging.info(f"Total rows processed: {total_rows}")
        logging.info(f"Tariff Groups created: {created_count}")
        logging.info(f"Tariff Groups updated: {updated_count}")
        logging.info(f"Simple Tariffs created: {simple_tariff_created_count}")
        logging.info(f"Simple Tariffs updated: {simple_tariff_updated_count}")
        logging.info(f"Warnings encountered: {len(warnings)}")
        logging.info(f"Errors encountered: {error_count}")

        if warnings:
            logging.info("")
            logging.info("Warnings:")
            for warning_msg in warnings:
                logging.warning(f"  {warning_msg}")

        if errors:
            logging.info("Rows causing errors and their exceptions:")
            for error_row, error_msg in errors:
                logging.info(f"Row: {error_row}, Error: {error_msg}")
    except Exception as e:
        logging.error(f"An error occurred: {e}")

if __name__ == "__main__":
    setup_logging("CreateOrUpdateTariffGroupAndBaseTariff")
    main()
