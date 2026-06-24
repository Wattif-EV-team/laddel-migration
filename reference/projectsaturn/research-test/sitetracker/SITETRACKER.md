# SiteTracker API Reference (Wattif Sandbox)

## Table of Contents
- [Authentication](#authentication)
- [API Basics](#api-basics)
- [Discovering Schema (Describe)](#discovering-schema-describe)
- [SOQL Queries](#soql-queries)
- [REST CRUD Operations](#rest-crud-operations)
- [Objects Reference](#objects-reference)
  - [sitetracker__Site__c](#sitetracker__site__c)
  - [Site_Relation__c](#site_relation__c)
  - [Account (Company)](#account-company)
- [Gotchas & Pitfalls](#gotchas--pitfalls)

---

## Authentication

OAuth2 password grant against Salesforce Connected App.

```python
import requests
from dotenv import load_dotenv
load_dotenv()

token_url = os.environ["SITETRACKER_TOKEN_URL"]  # https://test.salesforce.com/services/oauth2/token (sandbox)
resp = requests.post(token_url, data={
    "grant_type": "password",
    "client_id": os.environ["SITETRACKER_CLIENT_ID"],
    "client_secret": os.environ["SITETRACKER_CLIENT_SECRET"],
    "username": os.environ["SITETRACKER_USERNAME"],
    "password": os.environ["SITETRACKER_PASSWORD"],
})
token = resp.json()["access_token"]
instance_url = os.environ["SITETRACKER_INSTANCE_URL"]
headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
```

| Env var | Sandbox value |
|---|---|
| `SITETRACKER_TOKEN_URL` | `https://test.salesforce.com/services/oauth2/token` |
| `SITETRACKER_INSTANCE_URL` | `https://sitetracker-wattif--wattsand.sandbox.my.salesforce.com` |
| `SITETRACKER_USERNAME` | `oystein.tomassen@wattifev.com.wattsand` |

For **production**, token URL changes to `https://login.salesforce.com/services/oauth2/token`.

---

## API Basics

- **API version:** `v63.0`
- **Base path:** `{instance_url}/services/data/v63.0/`
- **Update method:** `PATCH` only — `PUT` returns `405 Method Not Allowed`
- **Delete:** `DELETE` on the record URL
- **Response format:** JSON by default

---

## Discovering Schema (Describe)

### List all objects
```
GET /services/data/v63.0/sobjects/
```

### Describe a specific object (fields, picklists, relationships)
```
GET /services/data/v63.0/sobjects/{ObjectApiName}/describe/
```

Returns `fields[]` array. Key properties per field:
- `name` — API name
- `label` — Human-readable label
- `type` — `string`, `picklist`, `multipicklist`, `reference`, `double`, `date`, `datetime`, `boolean`, `id`, `textarea`
- `updateable` — Can be written via PATCH/POST
- `calculated` — Formula field (read-only)
- `externalId` — Usable in upsert endpoint
- `referenceTo` — Target object for lookup fields
- `picklistValues[].value` — Enum values for picklist/multipicklist
- `length` — Max character length

### Save describe locally
```python
desc = requests.get(f"{base}/sobjects/sitetracker__Site__c/describe/", headers=headers).json()
with open("sitetracker_describe_sitetracker__Site__c.json", "w") as f:
    json.dump(desc, f, indent=2)
```

---

## SOQL Queries

```
GET /services/data/v63.0/query/?q={url_encoded_soql}
```

Response: `{ "totalSize": N, "done": true|false, "records": [...] }`

If `done=false`, paginate via `nextRecordsUrl`.

### Examples

```sql
-- Find site by custom ID
SELECT Id, Name, Site_ID__c, sitetracker__City__c FROM sitetracker__Site__c
WHERE Site_ID__c = 'W047201'

-- Find site relations for a site
SELECT Id, Name, Site__c, Company__c, Site_Relation_Role__c, previous_CPO__c
FROM Site_Relation__c WHERE Site__c = '{{site_sf_id}}'

-- Find Account by registration number (dedup pattern)
SELECT Id, Name, Business_Registration_Number__c FROM Account
WHERE Business_Registration_Number__c = '123456789'

-- Wildcard search
SELECT Id, Name FROM sitetracker__Site__c WHERE Name LIKE '%Adamstuen%'
```

### SOQL tips
- String values in single quotes: `WHERE Name = 'Foo'`
- URL-encode the entire query string
- Use `LIMIT N` and `OFFSET N` for pagination
- `SELECT COUNT() FROM Object WHERE ...` for counts
- Multi-picklist: `WHERE EV_Connector_Type__c INCLUDES ('CCS2')`

---

## REST CRUD Operations

### Create
```
POST /services/data/v63.0/sobjects/{ObjectApiName}/
Body: { "Field__c": "value", ... }
```
Returns `201` with `{ "id": "a0Hxxxx", "success": true }`.

### Read
```
GET /services/data/v63.0/sobjects/{ObjectApiName}/{id}
```

### Update
```
PATCH /services/data/v63.0/sobjects/{ObjectApiName}/{id}
Body: { "Field__c": "new_value" }
```
Returns `204 No Content` on success.

### Delete
```
DELETE /services/data/v63.0/sobjects/{ObjectApiName}/{id}
```
Returns `204 No Content`.

### Upsert by External ID
```
PATCH /services/data/v63.0/sobjects/{ObjectApiName}/{ExternalIdField}/{value}
Body: { ... }
```
**Note:** `Site_ID__c` is NOT an external ID. `Business_Registration_Number__c` is NOT an external ID. Neither supports upsert endpoint — use SOQL query + conditional create/update instead.

---

## Objects Reference

### sitetracker__Site__c

Represents a physical site/location.

**Key fields:**

| Field | Type | Notes |
|---|---|---|
| `Id` | id | Salesforce record ID (read-only) |
| `Name` | string | Descriptive name (e.g. "Adamstuen bygning 3") |
| `Site_ID__c` | string | Wattif site code (e.g. "W047201"), **max 9 chars**, NOT external ID |
| `sitetracker__Site_Status__c` | picklist | See values below |
| `sitetracker__Site_Type__c` | picklist | See values below |
| `sitetracker__Street_Address__c` | string | Street address |
| `sitetracker__Street_Address_2__c` | string | Address line 2 |
| `sitetracker__City__c` | string | City |
| `sitetracker__Zip_Code__c` | string | Postal code |
| `Country__c` | picklist | Format: `Norway(NOR)`, `Sweden(SWE)`, etc. |
| `sitetracker__Lat__c` | string | Latitude (text) |
| `sitetracker__Long__c` | string | Longitude (text) |
| `sitetracker__Location__Latitude__s` | double | Geolocation lat |
| `sitetracker__Location__Longitude__s` | double | Geolocation lon |
| `Owner_Type__c` | picklist | Ownership model |
| `EV_Connector_Type__c` | multipicklist | Semicolon-separated in API |
| `EV_Charging_Level__c` | multipicklist | Semicolon-separated in API |
| `Load_Management__c` | picklist | DLM protocol |
| `Open_Date__c` | date | |
| `Installed_Date__c` | date | |
| `Client_reference_ID__c` | string | External client ref |
| `Hubspot_Id__c` | string | |
| `Price__c` | currency | |

**Formula fields (read-only):** `sitetracker__Full_Address__c`, `sitetracker__Link_to_Map__c`

**Picklist values:**

`sitetracker__Site_Status__c`: `IC2 approved` · `Planning started` · `Installation started` · `Waiting for grid connection` · `Operational` · `Offline / Not operational` · `Decommissioned` · `Terminated` · `Under Migration`

`sitetracker__Site_Type__c`: `AIRPORT` · `ARENA` · `BUSINESS` · `CAMPING` · `CAR_DEALER` · `CONVENTION_CENTER` · `DEPOT` · `FACTORY` · `FLEET_GARAGE` · `HOSPITAL` · `HOTEL` · `HOUSING_ASSOCIATION` · `MUSEUM` · `OFFICE_BLDG` · `OTHER_ENTERTAINMENT` · `LEISURE PARK` · `PARK` · `PARKING_GARAGE` · `PARKING_LOT` · `RENTAL_CAR_RETURN` · `RESTAURANT` · `REST_STOP` · `SCHOOL` · `GAS_STATION` · `SHOPPING_CENTER` · `STADIUM` · `STREET_PARKING` · `WORKPLACE` · `OTHER`

`Owner_Type__c`: `W-WattifEV` · `J-Jointly Owned` · `C-ClientOwned` · `Caas` · `Client-owned-SLA`

`Load_Management__c`: `OCPP-DLM-1.6J` · `OCPP-DLM-2.0.1` · `OCPP-WATTIF-METER` · `LOCAL-MODBUS` · `LOCAL-EXTERNAL` · `NONE`

`Country__c`: `Austria(AUT)` · `Germany(DEU)` · `Ireland(IRL)` · `Netherlands(NLD)` · `Norway(NOR)` · `Sweden(SWE)` · `United Kingdom(GBR)`

`EV_Connector_Type__c` (multi): `Type 2` · `Type 2 cable` · `CCS2` · `CHADEMO` · `single type 2 socket`

`EV_Charging_Level__c` (multi): `Level-1-Schuko` · `Level 2 AC 22kWh` · `DC_above60` · `AC 7,4 kW` · `DC type (below 60 KWh)`

---

### Site_Relation__c

Links a Site to a Company (Account) with a role. Auto-number Name field (e.g. "006631").

**Updateable fields:**

| Field | Type | Notes |
|---|---|---|
| `Site__c` | reference → sitetracker__Site__c | Required |
| `Company__c` | reference → Account | Required |
| `Site_Relation_Role__c` | picklist | Role in relation |
| `Site_Relation_Start_Date__c` | date | |
| `previous_CPO__c` | string | Free text |
| `Grid_Supply__c` | reference | Links to grid supply record |

**Site_Relation_Role__c values:** `PARKING_OPERATOR` · `OWNER of GRID CONNECTION POINT` · `OWNER` · `OWNER of SITE` · `SUPPLIER` · `INSTALLER` · `CIVIL WORK` · `APPROVER` · `SUPPLIER of 4G` · `SUPPLIER of FIXED LINE` · `INVESTOR` · `PARTNER AND SALES CHANNEL`

**Note:** UI displays "INSTALLER of ELECTRO" but API picklist value is just `INSTALLER`.

---

### Account (Company)

Standard Salesforce Account object used as "Company" in Site Relations.

**Key fields:**

| Field | Type | Notes |
|---|---|---|
| `Name` | string | Company name (required) |
| `Business_Registration_Number__c` | string | Org number — NOT unique, NOT external ID |
| `Type` | picklist | Account type |
| `Industry` | picklist | |
| `BillingStreet`, `BillingCity`, `BillingPostalCode`, `BillingCountry` | string | Address |
| `Phone`, `Website` | string | |

**Dedup pattern** (no upsert available):
```python
soql = f"SELECT Id, Name FROM Account WHERE Business_Registration_Number__c = '{reg_no}'"
results = query(soql)
if results["totalSize"] > 0:
    account_id = results["records"][0]["Id"]
else:
    account_id = create("Account", payload)["id"]
```

**Type values:** `Customer` · `Partner` · `Partner and Sales Channel` · `Parking Operator` · `Manufacturer` · `Roaming Partner` · `Supplier` · `Sub-contractor` · `Investor` · `Other`

---

## Gotchas & Pitfalls

| Issue | Detail |
|---|---|
| PUT not supported | Use PATCH for updates. PUT returns 405. |
| Site_ID__c max 9 chars | Values like "W047201" work; longer strings are rejected. |
| No External IDs | Neither `Site_ID__c` nor `Business_Registration_Number__c` are external IDs. Cannot use upsert endpoint. |
| Numeric string padding | Salesforce pads lat/long strings: `"59.9423"` → `"59.942300000000000"`. Compare with `float()`. |
| Empty string → null | Salesforce stores `""` as `null`. Check both when verifying. |
| Multi-picklist format | Write: `"Type 2;CCS2"` (semicolon-separated). Read: same format. SOQL filter: `INCLUDES ('CCS2')`. |
| Name field on Site_Relation__c | Auto-number, read-only. Cannot be set on create. |
| State picklist | US state abbreviations only — not used for European sites. |
| Role display vs API | UI shows "INSTALLER of ELECTRO" but API value is `INSTALLER`. |
| Geolocation compound field | `sitetracker__Location__c` is a compound field. Set via `sitetracker__Location__Latitude__s` and `sitetracker__Location__Longitude__s` individually. |
| SOQL string escaping | Single quotes in values: escape as `\'`. Use `urllib.parse.quote` for URL encoding. |
