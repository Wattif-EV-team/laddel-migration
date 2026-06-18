import os
import json
import pandas as pd
import logging
from azure.identity import DeviceCodeCredential, TokenCachePersistenceOptions, AuthenticationRecord
try:
    # Newer SDK structure
    from msgraph.graph_service_client import GraphServiceClient
except ImportError:  # fallback if different version
    from msgraph import GraphServiceClient  # type: ignore
from msgraph.generated.models.search_request import SearchRequest
from msgraph.generated.models.search_query import SearchQuery
from msgraph.generated.models.entity_type import EntityType
from msgraph.generated.search.query.query_post_request_body import QueryPostRequestBody
from kiota_abstractions.native_response_handler import NativeResponseHandler
from kiota_http.middleware.options import ResponseHandlerOption
from msgraph.generated.drives.item.items.item.workbook.tables.item.rows.rows_request_builder import RowsRequestBuilder
from dotenv import load_dotenv

load_dotenv()

class GraphClient:
    SCOPES = ["User.Read", "Mail.Read", "Mail.Send", "Sites.Read.All", "Files.Read.All"]

    def __init__(self):
        self.client_id = os.getenv('clientId')
        self.tenant_id = os.getenv('tenantId')

        if not self.client_id or not self.tenant_id:
            raise RuntimeError("Environment variables 'clientId' and 'tenantId' must be set.")

        cache_dir = os.path.join(os.path.dirname(__file__), '.cache')
        os.makedirs(cache_dir, exist_ok=True)
        cache_path = os.path.join(cache_dir, "msal_token_cache.json")
        auth_record_path = os.path.join(cache_dir, "auth_record.json")

        token_cache_options = TokenCachePersistenceOptions(name=cache_path, allow_unencrypted_storage=True)

        if os.path.exists(auth_record_path):
            with open(auth_record_path, 'r') as auth_in:
                record_json_str = auth_in.read()
                deserialized_record = AuthenticationRecord.deserialize(record_json_str)
            self.device_code_credential = DeviceCodeCredential(
                client_id=self.client_id,
                tenant_id=self.tenant_id,
                cache_persistence_options=token_cache_options,
                authentication_record=deserialized_record
            )
        else:
            self.device_code_credential = DeviceCodeCredential(
                client_id=self.client_id,
                tenant_id=self.tenant_id,
                cache_persistence_options=token_cache_options
            )
            record = self.device_code_credential.authenticate(scopes=self.SCOPES)
            record_json = record.serialize()
            with open(auth_record_path, 'w') as auth_out:
                auth_out.write(record_json)

        self.user_client = GraphServiceClient(self.device_code_credential, self.SCOPES)

    async def search_file(self, file_name: str):
        request_body = QueryPostRequestBody(
            requests=[
                SearchRequest(
                    entity_types=[EntityType.DriveItem],
                    query=SearchQuery(
                        query_string=f'"{file_name}"'
                    )
                )
            ]
        )

        search_response = await self.user_client.search.query.post(request_body)
        results = []
        if not search_response or not getattr(search_response, 'value', None):
            return results
        first_container = search_response.value[0] if search_response.value else None
        if not first_container or not getattr(first_container, 'hits_containers', None):
            return results
        for hit_container in first_container.hits_containers or []:
            for hit in getattr(hit_container, 'hits', []) or []:
                resource = getattr(hit, 'resource', None)
                if not resource:
                    continue
                name = getattr(resource, 'name', None)
                web_url = getattr(resource, 'web_url', None)
                parent_ref = getattr(resource, 'parent_reference', None)
                site_id = getattr(parent_ref, 'site_id', None) if parent_ref else None
                drive_id = getattr(parent_ref, 'drive_id', None) if parent_ref else None
                file_id = getattr(resource, 'id', None)
                results.append({
                    "name": name,
                    "webUrl": web_url,
                    "siteId": site_id,
                    "driveId": drive_id,
                    "fileId": file_id
                })
        return results

    async def list_worksheets(self, drive_id: str, file_id: str):
        worksheets = await self.user_client.drives.by_drive_id(drive_id).items.by_drive_item_id(file_id).workbook.worksheets.get()
        if not worksheets or not getattr(worksheets, 'value', None):
            return []
        ws_list = getattr(worksheets, 'value', []) or []
        return [{"name": getattr(ws, 'name', None)} for ws in ws_list]

    async def list_excel_tables(self, drive_id: str, file_id: str):
        tables = await self.user_client.drives.by_drive_id(drive_id).items.by_drive_item_id(file_id).workbook.tables.get()
        if not tables or not getattr(tables, 'value', None):
            return []
        tbl_list = getattr(tables, 'value', []) or []
        return [{"name": getattr(table, 'name', None)} for table in tbl_list]

    async def fetch_table_data(self, drive_id: str, file_id: str, table_name: str):
        """Fetch workbook table data by table name or id with enhanced diagnostics.

        1. Enumerate tables first to resolve the real table ID (name vs GUID) – avoids 400 if name not accepted as ID.
        2. Log helpful context when GRAPH_DEBUG env var is set (any truthy value).
        3. Handle HttpResponseError to expose Graph error payload for troubleshooting.
        4. Support rows response shapes: list of objects each containing additional_data['values'] (list of lists).
        """
        debug = bool(os.getenv('GRAPH_DEBUG'))
        try:
            tables_response = await self.user_client.drives.by_drive_id(drive_id).items.by_drive_item_id(file_id).workbook.tables.get()
            if not tables_response or not getattr(tables_response, 'value', None):
                logging.warning(f"No tables returned for file_id={file_id}")
                return None

            # Build lookup (case-insensitive) for name -> table object
            table_lookup = {}
            for t in (getattr(tables_response, 'value', []) or []):
                nm = getattr(t, 'name', None)
                if nm:
                    table_lookup[nm.lower()] = t
            target_table = table_lookup.get(table_name.lower()) if table_name else None
            if not target_table:
                # Also attempt direct id match
                target_table = next((t for t in (getattr(tables_response, 'value', []) or []) if getattr(t, 'id', '').lower() == table_name.lower()), None)
            if not target_table:
                available = ", ".join(sorted(table_lookup.keys()))
                raise ValueError(f"Table '{table_name}' not found. Available tables: {available}")

            resolved_id = getattr(target_table, 'id', table_name)
            if debug:
                logging.info(f"Resolved table '{table_name}' -> id '{resolved_id}' (drive_id={drive_id}, file_id={file_id})")

            columns_resp = await self.user_client.drives.by_drive_id(drive_id).items.by_drive_item_id(file_id).workbook.tables.by_workbook_table_id(resolved_id).columns.get()
            column_names = []
            if columns_resp and getattr(columns_resp, 'value', None):
                for c in (getattr(columns_resp, 'value', []) or []):
                    column_names.append(getattr(c, 'name', None))
            if debug:
                logging.info(f"Columns ({len(column_names)}): {column_names}")

            # Use NativeResponseHandler to bypass Kiota JSON deserialization.
            # The Kiota serialization layer auto-coerces strings that look like
            # ISO 8601 times (e.g. "13220-04" → datetime.time(13,22,tz=-04:00))
            # when populating additional_data.  Reading the raw HTTP JSON avoids this.
            rows_cfg = RowsRequestBuilder.RowsRequestBuilderGetRequestConfiguration(
                options=[ResponseHandlerOption(NativeResponseHandler())],
            )
            rows_resp = await (
                self.user_client.drives.by_drive_id(drive_id)
                .items.by_drive_item_id(file_id)
                .workbook.tables.by_workbook_table_id(resolved_id)
                .rows.get(request_configuration=rows_cfg)
            )
            # rows_resp is the raw httpx.Response; parse JSON ourselves
            if hasattr(rows_resp, 'json') and callable(rows_resp.json):
                rows_json = rows_resp.json()
            elif isinstance(rows_resp, dict):
                rows_json = rows_resp
            else:
                raise TypeError(f"Unexpected response type from NativeResponseHandler: {type(rows_resp)}")

            raw_rows = rows_json.get("value", [])
            data = []
            for row_obj in raw_rows:
                for inner in row_obj.get("values", []):
                    data.append(inner)
            if debug:
                logging.info(f"Fetched {len(data)} data rows from table '{table_name}' (raw JSON, no SDK coercion).")

            if not data:
                logging.warning(f"No data found for table: {table_name} (id {resolved_id})")
                return None

            # Ensure column count alignment – pad or trim if mismatch
            max_len = max(len(row) for row in data)
            if len(column_names) < max_len:
                # Pad column names
                missing = max_len - len(column_names)
                column_names.extend([f"_col{idx}" for idx in range(len(column_names), len(column_names) + missing)])
            trimmed_data = [row[:len(column_names)] + ([None] * (len(column_names) - len(row))) for row in data]

            df = pd.DataFrame(trimmed_data, columns=column_names)
            return df
        except Exception as e:
            # Try to surface Graph error details
            try:
                from azure.core.exceptions import HttpResponseError
                if isinstance(e, HttpResponseError):
                    logging.error(f"Graph HttpResponseError status={e.status_code}")
                    try:
                        # e.response is azure.core.pipeline.transport._requests_basic.RequestsTransportResponse
                        resp_obj = getattr(e, 'response', None)
                        body_text = None
                        if resp_obj:
                            txt_attr = getattr(resp_obj, 'text', None)
                            if callable(txt_attr):
                                try:
                                    body_text = txt_attr()
                                except Exception:  # pragma: no cover
                                    body_text = None
                            if body_text is None:
                                body_text = getattr(resp_obj, 'content', '')
                        else:
                            body_text = '<no response object>'
                        logging.error(f"Graph error body: {body_text}")
                    except Exception:
                        pass
            except ImportError:
                pass
            logging.error(f"Failed to fetch table '{table_name}' (drive_id={drive_id}, file_id={file_id}): {e}")
            raise
