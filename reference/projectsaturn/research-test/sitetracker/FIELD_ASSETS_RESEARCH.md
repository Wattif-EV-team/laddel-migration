# SiteTracker Field Assets Research

## Summary

| Object | API Name | Records | Purpose |
|--------|----------|---------|---------|
| Field Asset | `sitetracker__Field_Asset__c` | 10,000 | Individual physical asset (charger, router, meter, etc.) installed at a site |
| Site Inventory | `sitetracker__Site_Inventory__c` | 4,286 | Aggregate count per (Site + Item type) — tracks how many of each Item are installed/available at a site |
| Item | `sitetracker__Item__c` | 139 (74 used by FAs) | Product catalog — defines charger models, routers, SIM cards, meters etc. |

---

## Object Relationships

```
Site (sitetracker__Site__c)
 ├── Site Inventory (sitetracker__Site_Inventory__c)    [1 per unique Item at a Site]
 │    ├── sitetracker__Site__c → Site
 │    ├── sitetracker__Item__c → Item
 │    └── sitetracker__Installed__c = count of FAs with Status=Installed
 │
 └── Field Asset (sitetracker__Field_Asset__c)          [1 per physical device]
      ├── sitetracker__Site__c → Site                   (REQUIRED, direct link)
      ├── sitetracker__Item__c → Item                   (REQUIRED)
      ├── sitetracker__Site_Inventory__c → Site Inventory (nullable but 100% populated)
      └── sitetracker__Parent__c → Field Asset          (optional hierarchy, 0.3% usage)

Item (sitetracker__Item__c)
 └── Product catalog entry (e.g. "DEFA Power", "Teltonika RUT200")
```

**Key insight:** Site Inventory appears to be **auto-created by SiteTracker triggers** when a Field Asset is created. Evidence:
- 100% of Field Assets have a Site Inventory link (0 without)
- Site Inventory's `Name` is auto-number (SI-000xxx, read-only)
- The SI always matches the FA's Site + Item combination
- SI `sitetracker__Installed__c` aggregates the count of FAs in "Installed" status

**Therefore, to create Field Assets we likely only need to provide:** `sitetracker__Site__c`, `sitetracker__Item__c`, and `sitetracker__Status__c` — and the system auto-manages Site Inventory.

---

## Field Asset (`sitetracker__Field_Asset__c`)

### Required Fields (for CREATE)

| Field | Label | Type | Notes |
|-------|-------|------|-------|
| `sitetracker__Item__c` | Item | reference → Item | Product type (e.g. "DEFA Power") |
| `sitetracker__Site__c` | Site | reference → Site | Which site it belongs to |
| `sitetracker__Status__c` | Status | picklist | Must be one of the enum values below |

### Auto-Generated Fields (read-only)

| Field | Label | Type | Notes |
|-------|-------|------|-------|
| `Asset__c` | Asset Identity | auto-number | Format: `WAS000052318` — company property identifier |
| `sitetracker__Identifier__c` | Identifier | auto-number | Format: `FA-00052319` — auto-generated, used as Serial if Serial is blank |
| `sitetracker__Site_Inventory__c` | Site Inventory | reference | Auto-populated by SiteTracker trigger |

### Commonly Used Createable Fields

| Field | Label | Type | Population % | Notes |
|-------|-------|------|-------------|-------|
| `Name` | Field Asset Name | string(80) | 100% | User-settable naming convention (see below) |
| `sitetracker__Serial__c` | Serial | string | 100% | Charger serial number or device identifier |
| `sitetracker__Status__c` | Status | picklist | 100% | See picklist values below |
| `sitetracker__Install_Date__c` | Install Date | date | 99.5% | When asset was (last) installed |
| `sitetracker__Original_Install_Date__c` | Original Install Date | date | 99.5% | First-ever install date |
| `Ownership__c` | Ownership | picklist | 57.2% | Who owns the hardware |
| `iccID__c` | iccID | string | 17.3% | SIM card ICCID (chargers with 4G) |
| `Location__c` | Location | string | 15.7% | Parking spot / position label (e.g. "2005", "P31") |
| `sitetracker__Complete_Kit__c` | Complete Kit | boolean | default: False | Whether asset is part of a complete kit |
| `Password__c` | Password | string(60) | 5.2% | Device password (routers, some chargers) |
| `Factory_Default_Password__c` | Factory Default Password | string(50) | — | Factory default password for the device |
| `sitetracker__Notes__c` | Notes | textarea | 3.8% | Free-text notes |
| `IP_Address__c` | IP Address | string | 2.8% | Device IP address |
| `MAC__c` | MAC | string | 1.8% | MAC address (mostly routers) |
| `IMEI__c` | IMEI | string | 1.2% | IMEI number (routers/modems) |
| `Project__c` | Project | reference → Project | 1.3% | Linked SiteTracker project |
| `URL_Management__c` | URL Management | url | 0.3% | Link to management portal (e.g. Teltonika RMS) |
| `URL_Device__c` | URL Device | url | 0.1% | Direct device URL |
| `sitetracker__Parent__c` | Parent | reference → Field Asset | 0.3% | Parent FA (for hierarchy) |
| `sitetracker__Quantity__c` | Quantity | double | 100% | Always 1.0 for uniquely tracked items |
| `Asset_Credential__c` | Asset Credential | string | <0.1% | Rarely used |

### Picklist Values

**`sitetracker__Status__c` (Status):**
| Value | Count | Usage |
|-------|-------|-------|
| `Installed` | 9,723 | Active, deployed asset |
| `Decommissioned` | 184 | Retired/removed |
| `Available` | 79 | In stock, not yet installed |
| `Not Available` | 10 | Broken/unavailable |
| `Pending Transfer` | 4 | Being moved between sites |

**`Ownership__c` (Ownership):**
| Value | Count | Notes |
|-------|-------|-------|
| `CU-Customer Owned` | 4,644 | Customer owns the hardware |
| *(NULL)* | 4,284 | Not set (mostly older records) |
| `W-WattifEV Owned` | 742 | Wattif owns the hardware |
| `C-Client Owned` | 252 | Client-owned |
| `Caas` | 63 | Charging-as-a-Service |
| `J-Jointly Owned` | 15 | Jointly owned |

### Naming Conventions

| Pattern | Example | Count | Format |
|---------|---------|-------|--------|
| `NOR{site_code}-{position}` | `NOROSLFGS07-2005` | 2,053 | Norwegian sites: country + site code + position |
| `SWE{site_code}\|{position}` | `SWEHAGVAS1\|Laddplats 68-69` | 1,553 | Swedish sites with pipe separator |
| `W0{legacy_code}` | `W046017009` | 1,019 | Legacy format (older Norwegian assets) |
| `{prefix}\|{suffix}` | (pipe separated) | 117 | Some use prefix-suffix pattern |

### Parent/Child Hierarchy

- Only 32 out of 10,000 FAs use `sitetracker__Parent__c`
- Used for: Payment terminals attached to DC chargers (e.g. Payter Apllo → Alpitronic HYC 50kW)
- `sitetracker__Top_Level_Parent__c` always set when Parent is set
- **For charger migration: hierarchy is likely not needed** (it's only for accessories)

---

## Site Inventory (`sitetracker__Site_Inventory__c`)

### Purpose

Site Inventory is an **aggregate/counting record** that answers: "How many of Item X exist at Site Y, and what's their status?"

It's a many-to-one aggregation of Field Assets. The `Installed`, `Available`, and `Not Available` counts reflect the number of Field Assets in each status.

### Fields

| Field | Label | Type | Notes |
|-------|-------|------|-------|
| `Name` | Site Inventory Name | auto-number | Format: `SI-003003` (read-only) |
| `sitetracker__Site__c` | Site | reference → Site | Which site |
| `sitetracker__Item__c` | Item | reference → Item | Which product type |
| `sitetracker__Installed__c` | Installed | double | Count of FAs with Status=Installed |
| `sitetracker__Available__c` | Available | double | Count of FAs with Status=Available |
| `sitetracker__Not_Available__c` | Not Available | double | Count of FAs with Status=Not Available |
| `Pending_Transfer__c` | Pending Transfer | double | Count of FAs pending transfer |
| `sitetracker__Recalculate__c` | Recalculate | boolean | Trigger flag to recalculate counts |

### Key Characteristics

- **Auto-managed**: Created automatically when a Field Asset is provisioned at a (Site, Item) pair
- **One per (Site + Item)** pair (with rare duplicates from data issues — 48/4286)
- **Counts are maintained by triggers**: When FA status changes, SI counts update
- **You do NOT need to create SI manually** — just create the Field Asset

### Createable Fields (if manual creation needed)

| Field | Nillable |
|-------|----------|
| `sitetracker__Site__c` | Yes |
| `sitetracker__Item__c` | Yes |
| `sitetracker__Installed__c` | Yes |
| `sitetracker__Available__c` | Yes |
| `sitetracker__Not_Available__c` | Yes |
| `Pending_Transfer__c` | Yes |
| `sitetracker__Recalculate__c` | No (defaults False) |

---

## Item (`sitetracker__Item__c`)

### Purpose

Product catalog / item type definitions. Each Item represents a **model** of equipment (not a specific physical unit).

### Key Fields

| Field | Label | Type | Notes |
|-------|-------|------|-------|
| `Name` | Item Name | string | Product name (e.g. "DEFA Power", "Teltonika RUT200") |
| `sitetracker__Category__c` | Category | picklist | `Charger`, `Network`, `Electrical`, `Payment Terminal`, `Other` |
| `sitetracker__Type__c` | Type | picklist | `Material`, `Tool/Equipment`, `Labor`, `Service`, `Expense` |
| `sitetracker__Tracking_Method__c` | Tracking Method | picklist | `Uniquely Tracked` (all FA items use this) |
| `Sockets__c` | Sockets | double | Number of charging sockets (0 for non-chargers) |
| `sitetracker__Manufacturer__c` | Manufacturer | reference → Account | Manufacturer (rarely populated) |
| `sitetracker__Item_Number__c` | Item Number | string | Part number |
| `sitetracker__Description__c` | Description | string | Item description |

### Items Used for Field Assets (74 in use)

**Charger Items (55):**
| Item Name | Sockets |
|-----------|---------|
| ABB TAC-W22-32A-4G | 1 |
| ABB Terra 184HC CC | 2 |
| Alpitronic HYC 200 (100kW and 200kW) | 2 |
| Alpitronic HYC 50kW | 2 |
| Charge Amps Aura | 2 |
| Charge Amps Dawn | 1 |
| CTEK CC2 DUAL | 2 |
| CTEK CC2 SINGLE | 1 |
| CTEK CC3 DUAL | 2 |
| CTEK CC3 SINGLE | 1 |
| DEFA Power | 1 |
| DEFA Power S | 1 |
| Easee Charge | 1 |
| Easee Charge Core | 1 |
| Easee Home | 1 |
| Garo Entity | 1 |
| Garo GLB | 1 |
| Garo GTB | 2 |
| Garo LS4 | 2 |
| Garo Twin | 2 |
| Keba P30 X series | 1 |
| Rolec Quantum Dual 22kW | 2 |
| Schneider EvLink Pro AC | 1 |
| Vestel EVC04-AC22 | 1 |
| Zaptec Go | 1 |
| Zaptec Pro | 1 |
| *(and more...)* | |

**Network Items (14):**
| Item Name | Purpose |
|-----------|---------|
| Enegic Monitor Ethernet | Energy monitoring |
| Fortigate FG40 4G / WiFi | Firewall/router |
| RUT241 LTE router | 4G router |
| RUT951 - 4G router dual SIM - Wifi | Router |
| RUT956 Industriell LTE-ruter | Industrial router |
| Simcard-50GB / 720MB / Monthly / NCG | SIM cards |
| TRB140 / TRB140 - 4G modem | 4G modem |
| Teltonika RUT200 / RUT202 / RUT240 | Routers |
| Teltonika TAP 100 | Network probe |
| Teltonika 956 | Router |

---

## Category-Specific Field Usage

### Chargers (9,755 records — 97.5%)

Fields consistently populated beyond mandatory:
| Field | Usage | Notes |
|-------|-------|-------|
| `sitetracker__Serial__c` | 100% | Charger serial number |
| `sitetracker__Install_Date__c` | 99.6% | Install date |
| `Ownership__c` | 56.9% | Ownership model |
| `iccID__c` | 17.0% | SIM ICCID for 4G chargers |
| `Location__c` | 15.9% | Parking position label |
| `Password__c` | 3.8% | Device access password |

### Network Equipment (227 records — 2.3%)

Fields consistently populated beyond mandatory:
| Field | Usage | Notes |
|-------|-------|-------|
| `sitetracker__Serial__c` | 100% | Router serial |
| `sitetracker__Install_Date__c` | 97.8% | Install date |
| `Ownership__c` | 67.4% | Ownership |
| `Password__c` | 62.1% | Router password |
| `MAC__c` | 50.7% | MAC address |
| `IMEI__c` | 46.3% | Router IMEI |
| `iccID__c` | 31.7% | SIM ICCID |
| `sitetracker__Parent__c` | 13.7% | Linked to parent (charger) |
| `IP_Address__c` | 10.6% | IP address |
| `URL_Management__c` | 7.0% | Teltonika RMS link |

---

## Creation Dates Analysis

| Period | Records | Notes |
|--------|---------|-------|
| 2022 (Oct) | 11 | Earliest records, minimal data |
| 2023 | ~51 | Slow growth, manual entry |
| 2024 (Jan-Mar) | ~2 | Low activity |
| 2024 (Apr 10) | 94 | First bulk import |
| 2024 (rest) | ~70 | Gradual |
| 2025 (Jan-Feb) | ~70 | Steady |
| 2025 (Mar 27-28) | **2,764** | Major bulk migration |
| 2025 (Apr 30) | **1,353** | Another bulk migration |
| 2025 (Jul 29-30) | **2,692** | Latest bulk migration |
| 2025 (rest) | ~1,000 | Ongoing provisioning |

**Observation:** Bulk migrations account for ~80% of records. Older records (2022-2023) often have gaps in `Ownership__c` and `Location__c`.

---

## How to Link a Field Asset to a Site

The link is **direct** via `sitetracker__Site__c` (reference field). You need the Salesforce ID of the Site record.

```
Field Asset.sitetracker__Site__c = Site.Id
```

There is no intermediate table needed. The Site Inventory record is auto-created/managed.

---

## Migration Strategy (Preliminary)

To create a Field Asset, the **minimum required payload** is:

```json
{
    "Name": "NOROSLFGS07-2005",
    "sitetracker__Item__c": "<Item SF ID>",
    "sitetracker__Site__c": "<Site SF ID>",
    "sitetracker__Status__c": "Installed",
    "sitetracker__Serial__c": "5AC00R10F",
    "sitetracker__Install_Date__c": "2026-04-07",
    "sitetracker__Original_Install_Date__c": "2026-04-07",
    "Ownership__c": "CU-Customer Owned"
}
```

**Lookup requirements:**
1. **Item** — Resolve charger model name → Item SF ID (from 74 known items)
2. **Site** — Resolve project_code → Site SF ID (from existing `sitetracker_site_mapping`)

**Optional enrichment fields:**
- `Location__c` — Parking spot/position
- `iccID__c` — SIM ICCID
- `Password__c` — Device password
- `IP_Address__c`, `MAC__c`, `IMEI__c` — Network details
- `URL_Management__c` — Management portal URL

---

## Gotchas

| Issue | Detail |
|-------|--------|
| SI auto-created | Don't try to manually create Site Inventory — it's trigger-managed |
| Name max 80 chars | Field Asset Name limited to 80 characters |
| Asset__c read-only | Auto-number `WAS000052318` cannot be set |
| Identifier read-only | Auto-number `FA-00052319` cannot be set |
| Item must exist | The Item record must already exist in the catalog |
| Serial not enforced unique | `sitetracker__Serial__c` is not a unique field |
| Quantity always 1 | For uniquely tracked items, Quantity is always 1.0 |
| Parent rarely used | Only for payment terminals attached to DC chargers (32/10000) |
| SOQL 10k limit | Default SOQL query returns max 10,000 rows |
