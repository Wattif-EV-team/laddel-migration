# Field mapping — Charge Point (create)

> **Status:** ✅ **Implemented (first iteration).** Maps the **`laddel`** source onto the
> Ampeco **create charge point** payload (`chargePointV2Write`).
>
> - View: [301_target_charge_points.sql](../../sql/301_target_charge_points.sql) — 6 077 rows
>   (4 892 masters + 1 185 satellites), every count below verified live on 2026-09-10.
> - Mapping table: [007_charge_point_mapping.sql](../../sql/007_charge_point_mapping.sql)
>   (with adoption columns).
> - Step: `src/laddel_migration/steps/ampeco/charge_points.py`, registered after
>   `locations` in the `ampeco` profile. Two passes filtered on `communicationMode`;
>   masters are looked up by `filter[networkId]` and adopted when they already exist.
>
> All ten open questions from the first draft were answered on 2026-09-10 — see
> [Decisions](#decisions-2026-09-10). Remaining ⚠️ markers are notes/risks, not blockers.

## Endpoint & payload source

- **API (source of truth):** `POST /public-api/resources/charge-points/v2.0`
  (`operationId: chargePointCreate`, request schema **`chargePointV2Write`**).
- **Update:** `PATCH /public-api/resources/charge-points/v2.0/{chargePoint}`
  (`operationId: chargePointUpdate`, schema `chargePointV2Patch`).
- **Deprecated — do NOT target:** the whole `/public-api/resources/charge-points/v1.0`
  family (`chargePointCreateDeprecated`, `chargePointUpdateDeprecated`, …).
- **Side-car resources (separate calls, out of scope for this doc):**
  - `POST .../v2.0/{chargePoint}/evses` — EVSEs (grain = `laddel.charger`, see below).
  - `POST .../v2.0/{chargePoint}/smart-charging` — DLM / electrical configuration.
  - `PUT .../v2.0/{chargePoint}/shared-partners` — shared partners.
  - `POST .../v2.0/{chargePoint}/notes` — notices.

> ℹ️ **New fields vs. the reference project.** Compared with `reference/projectsaturn`'s
> `CreateOrUpdateChargePoints.py` / `312_target_chargepoints.sql`, the current
> `chargePointV2Write` schema adds: **`communicationMode`**, **`ocppConnectedChargePointId`**,
> `monitoringEnabled`, `autoRecoveryEnabled`, `uptimeTrackingEnabled`, `sharingCode`,
> `networkType`, `usesRenewableEnergy`, `electricityCostReimbursementIntegrationId`,
> `installationAndMaintenanceCompanyId`, `countryStationId`,
> `calibrationLawDataAvailability`, `powerSharing`, `partner.contactId`, and
> `security.currentProfile` / `hardwareEnabledProfile` / `desiredProfileStatus`.
> It also **deprecates** `managedByOperator` (→ `communicationMode`),
> `enableAutoFaultRecovery` (→ `monitoringEnabled` + `autoRecoveryEnabled`, removal
> targeted v2.1 / Q2 2026) and `partner.notice` (→ top-level `noticeId`).
> The YAML is authoritative; the reference view's `security_desiredProfile = 2` and
> `enableAutoFaultRecovery` are **not** carried over.

---

## Grain & the charger ⇄ EVSE ⇄ charge point model

| Ampeco | `laddel` | Notes |
|---|---|---|
| Charge point | `charger` (**one row = one charge point**) | Carries the box + network config. |
| EVSE | `charger` (**same row**) | A `laddel.charger` is conceptually an EVSE. |
| Physical box / OCPP endpoint | `charger.ocpp_id` | `UNIQUE (ocpp_id, socket_id)` — one box, many sockets. |

**Decided grain (Q1, confirmed): one Ampeco charge point per `laddel.charger`.**
`mapping_key = 'Laddel|Charger|' || charger_id`, mapping table
`target.charge_point_mapping` (`0xx`, to be created) with `target_charge_point_id`.
The alternative shape (one CP per `ocpp_id` with N EVSEs) was considered and **rejected**.

Because several `charger` rows can share one `ocpp_id` (one physical box), only **one**
of them may own the OCPP connection. The rest are modelled as Ampeco **satellites**:

- **Master** = the charger with the **lowest `socket_id`** within its `ocpp_id`
  → `communicationMode = 'direct_ocpp'`, sends the `network` + `security` blocks.
- **Satellite** = every other charger sharing that `ocpp_id`
  → `communicationMode = 'via_ocpp_connected_charge_point'` +
  `ocppConnectedChargePointId` = the master's **Ampeco** charge point id.

Verified in scope (`organization.migration_status = 'READY'` + eligible):

| Metric | Value |
|---|---|
| Chargers | **6 077** (6 053 active, 24 inactive) |
| Facilities / organizations | 441 / 400 |
| Distinct `ocpp_id` (= masters) | **4 892** |
| Satellites (`socket_id` > box minimum) | **1 185** |
| Boxes with 1 socket | 4 712 |
| Boxes with 2–20 sockets | 180 (largest = 20, `EVB-P2309218`, facility 274) |
| `ocpp_id` spanning >1 facility | **0** (whole source too) |
| Boxes with mixed `active` values | **0** |

> ⚠️ **`socket_id` is not a small ordinal.** It is `1` for 4 168 chargers and `101–199`
> for 564, but for EVBox boxes it is a large connector serial (e.g. `1 829 181`,
> `2 305 204`). "Lowest `socket_id` = master" is still deterministic and stable, but it
> is **not** "socket 1" — do not assume `socket_id = 1` identifies the master.

> ⚠️ **Step ordering consequence.** The satellite rows need the master's *Ampeco* id, so
> the charge-points step must run **two passes**: create/patch all masters first, then
> re-read the view (masters' ids now in `charge_point_mapping`) and create the
> satellites. The view exposes `is_master` and `ocppConnectedChargePointId` so the step
> can partition on it.

### Idempotency: lookup before create (Q9)

Unlike partners / locations / partner contracts, charge points **do** have a reliable
natural key in Ampeco: `network.id`. The step therefore keeps the reference project's
`CreateOrUpdateChargePoints.py` behaviour — when `target_charge_point_id` is NULL:

1. `GET /public-api/resources/charge-points/v2.0?filter[networkId]={network_id}`;
2. if a record comes back → **adopt** it (`PATCH` + write the id into
   `charge_point_mapping`);
3. otherwise → `POST` (create) and write the returned id.

> ⚠️ **Satellites have no `network.id`**, so they cannot be looked up this way. For
> satellite rows the lookup step is skipped and every write is a fresh create — an
> aborted run that created satellites without persisting their mapping row would
> duplicate them. Mitigate by writing the mapping row immediately after each create
> (the existing atomic-mapping-write pattern, Guide §7).
>
> Because adoption is possible, `charge_point_mapping` **should** carry the adoption
> columns (`existed_before`, `matched_by`, `snapshot`) that partners/locations omit.

---

## Scope & batch gate

Same gate as [304_target_location.sql](../../sql/304_target_location.sql) /
[306_target_partner_contracts.sql](../../sql/306_target_partner_contracts.sql):

```sql
FROM       `laddel`.`charger`  c
JOIN       `laddel`.`facility` f  ON f.facility_id = c.facility_id
JOIN       `laddel`.`organization` o ON o.organization_id = f.organization_id
JOIN       `target`.`facility_migration_eligibility` fme ON fme.facility_id = f.facility_id
WHERE      o.migration_status = 'READY'
  AND      fme.should_not_migrate = 0
```

- Inactive chargers (`active = 0`, 24 rows) are **not** filtered out — see `status`.
- **Personal chargers (`charger_reference LIKE 'LDB%'`) are 0 in scope** (3 914 exist in
  the whole source, all outside `READY`). The `personal` branch is therefore specified
  but dead code this iteration.

## Source tables

```
laddel.charger                    c    -- grain
   └─ laddel.facility             f    ON f.facility_id = c.facility_id
        ├─ laddel.facility_information fi ON fi.facility_id = f.facility_id      -- is_hidden, price_id
        │     └─ laddel.price_information pi ON pi.price_id = fi.price_id        -- priceModel
        ├─ laddel.facility_contact fc ON fc.facility_id = f.facility_id          -- LEFT JOIN
        │     └─ laddel.customer   cu ON cu.customer_id = fc.customer_id         -- vat_registered
        └─ laddel.organization     o  ON o.organization_id = f.organization_id   -- migration_status
target.facility_migration_eligibility fme   -- should_not_migrate, project_code
target.location_mapping               lm    -- Laddel|Facility|{facility_id} -> target_location_id
target.partner_contract_mapping       pcm   -- Laddel|Facility|{facility_id} -> target_partner_contract_id
target.partner_mapping                pm    -- Laddel|Customer|{customer_id} -> target_partner_id
target.charge_point_mapping           cpm   -- Laddel|Charger|{charger_id}   -> target_charge_point_id  (NEW, 0xx)
target.charge_point_mapping           mst   -- self-join for the master's id (satellites)
```

- `facility_contact` → `customer` is a `LEFT JOIN` by convention, but **all 6 077 in-scope
  chargers do have a customer** (0 missing).
- `charger_information` and `charger_coordinates` are effectively empty for in-scope
  chargers (**0** and **14** rows respectively) — no usable per-charger description,
  price or geoposition.

## Source column reference (`laddel.charger`)

| Column | Type | Used for | Notes |
|---|---|---|---|
| `charger_id` | int PK | `mapping_key` | grain |
| `charger_name` | varchar(255) | *(unused)* | Unique in scope (6 077 distinct), 1–74 chars, but inconsistent: 5 049 equal `charger_reference` while 1 028 are decorated, e.g. `Leili. 04 - LDZ1070`, `LDE1040 (LH) - P 65`. **Not** used for `name` — see Q2. |
| `active` | tinyint(1) NOT NULL | (EVSE `status`) | 6 053 = 1, 24 = 0 in scope. |
| `brand` | varchar(64) NOT NULL | *(unused)* | zaptec 1 591, easee 1 415, evbox 1 368, garo 882, enua 432, other 153, defa 132, chargeamps 52, schneider 45, amina 4, "charge amps" 3. All AC. No `modelId` mapping exists. |
| `facility_id` | int FK | location / partner / contract joins | |
| `charger_reference` | varchar(64) **UNIQUE** NOT NULL | `name`, `personal` test | 1–14 chars. Prefixes in scope: LDH 2 786, LDE 1 117, LDA 740, LDZ 653, LDN 362, LDG 266, LDP 65, LDS 44, LDC 24, + 18 strays (`GKV`, `SLE`, `W`, `WW`, `0`, …). **`LDB` = 0 in scope.** |
| `ocpp_id` | varchar(64) NOT NULL | `network.id`, master/satellite grouping | 3–20 chars, 100 % `[A-Za-z0-9._-]`. `UNIQUE (ocpp_id, socket_id)`. |
| `socket_id` | int NOT NULL DEFAULT 1 | master election | See the warning above — large serials for EVBox. |
| `installation_id` | varchar(255) FK | *(unused)* | NULL for 4 078 / 6 077. `installation` only has `id, name, facility_id, emabler_id`. |
| `creation_date` | datetime(3) | `integratedAt` | **0 NULLs in scope.** |
| `use_ocpi_integration` | tinyint(1) | *(unused)* | 0 for every in-scope charger. |
| `is_whitelist_enabled` | tinyint(1) NOT NULL | `partner.accessType` | 199 chargers have 1 (Q5/Q6). ⚠️ 11 boxes mix whitelisted and non-whitelisted chargers — harmless here, since `accessType` is per charge point and the grain is per charger. |

---

## Field mapping

Legend — **Default** = constant we emit; `c./f./fi./pi./cu.` = source column;
*(omit)* = deliberately not sent; ⚠️ = implementation note / risk to watch.

> **Nesting convention.** Following MigrationPatternGuide §5.3 the view emits flat columns
> named `<parent>_<child>` (`network_id`, `partner_contractId`, …) and the step folds them
> into the nested JSON objects. JSON-array fields (`tags`, `capabilities`,
> `subscription_planIds`) are emitted as JSON **strings** and `json.loads`-ed by the step.

### Bookkeeping (not part of the payload)

| View column | Value | Notes |
|---|---|---|
| `mapping_key` | `CONCAT('Laddel|Charger|', c.charger_id)` | Source table name in the middle segment (Guide §5.1). |
| `source_label` | `CONCAT(c.charger_reference, ' (chg=', c.charger_id, ', fac=', c.facility_id, ')')` | Logging only. |
| `target_charge_point_id` | `cpm.target_charge_point_id` | NULL ⇒ not created yet. |
| `is_master` | `c.socket_id = MIN(c.socket_id) OVER (PARTITION BY c.ocpp_id)` | Drives the two-pass ordering. ✅ MySQL 8 window functions inside a view verified on the managed host. |
| `master_mapping_key` | `CONCAT('Laddel|Charger|', FIRST_VALUE(c.charger_id) OVER (PARTITION BY c.ocpp_id ORDER BY c.socket_id))` | Join key for the self-lookup. |
| `box_size` | `COUNT(*) OVER (PARTITION BY c.ocpp_id)` | Drives the `[MASTER]`/`[SLAVE]` name suffix. |

### Identity & type

| API field | View column | Type | Req | Source / value | Notes |
|---|---|---|:--:|---|---|
| `name` | `name` | string | **yes** | **`CONCAT('NOR', c.charger_reference, suffix)`** — see below | **Decided (Q2).** Admin-facing only, not shown to end users. |
| `type` | `type` | enum | **yes** | see rule below | `private` \| `public` \| `personal`. |
| `pin` | — | string | no | *(omit)* | Personal-only. 0 personal rows in scope. If ever needed: 4-digit hash of `charger_id` (reference-project pattern). |
| `externalId` | — | string | no | *(omit — leave blank)* | **Decided (Q3).** No external id is sent. Idempotency is carried by `network.id` + `charge_point_mapping` instead. |
| `status` | `status` | enum | **yes** | `'enabled'` (Default, always) | **Decided (Q4).** API-required, but real availability is set on the **EVSE**, so the CP is unconditionally `enabled` — including for the 24 `active = 0` chargers, whose EVSE will carry the disabled state. |
| `id`, `operatorId` | — | — | — | *(omit)* | Operator-scoped token; `id` is server-assigned. |

**`name` rule (Q2):** `'NOR'` prefix + `charger_reference`, suffixed with the OCPP role
**only when the box carries more than one EVSE**:

```sql
CONCAT('NOR', c.charger_reference,
       CASE WHEN box_size > 1 AND is_master THEN ' [MASTER]'
            WHEN box_size > 1               THEN ' [SLAVE]'
            ELSE '' END)
```

Verified in scope — **6 077 / 6 077 names are unique**, 4–26 chars:

| Suffix | Chargers | Example |
|---|--:|---|
| *(none — single-EVSE box)* | 4 712 | `NORLDH4793` |
| ` [MASTER]` | 180 | `NORLDE1040 [MASTER]` |
| ` [SLAVE]` | 1 185 | `NORLDE1056 [SLAVE]` |

> The `MASTER`/`SLAVE` wording mirrors the OCPP master/satellite relationship expressed by
> `communicationMode`; it is an operator convenience label, not an API concept.

**`type` rule (as instructed):**

```sql
CASE
    WHEN c.charger_reference LIKE 'LDB%'                  THEN 'personal'
    WHEN fi.is_hidden = 0
     AND pi.priceModel <> 'SUBSCRIPTION'
     AND cu.vat_registered = 1                            THEN 'public'
    ELSE 'private'
END
```

Verified counts in scope: **private 6 063** (439 facilities), **public 14** (2 facilities:
the two COMMISSION hotels), **personal 0**.
Underlying matrix (`is_hidden` / `priceModel` / `vat_registered`):

| is_hidden | priceModel | vat_registered | chargers | facilities | → type |
|:--:|---|:--:|--:|--:|---|
| 1 | SUBSCRIPTION | 0 | 4 278 | 276 | private |
| 0 | SUBSCRIPTION | 0 | 1 402 | 122 | private |
| 0 | SUBSCRIPTION | 1 | 201 | 11 | private |
| 1 | SUBSCRIPTION | 1 | 99 | 9 | private |
| 1 | MARKUP | 0 | 42 | 11 | private |
| 0 | MARKUP | 0 | 41 | 10 | private |
| 0 | COMMISSION | 1 | 14 | 2 | **public** |

> ⚠️ `pi.priceModel` comes through a `LEFT JOIN` — a facility with no `price_information`
> would make the `<> 'SUBSCRIPTION'` test NULL and fall through to `private`, which is the
> safe default. All in-scope facilities do have a price row.

### Placement / relationships

| API field | View column | Type | Req | Source / value | Notes |
|---|---|---|:--:|---|---|
| `locationId` | `locationId` | int | yes\* | `lm.target_location_id` via `Laddel|Facility|{facility_id}` | Required for `public`/`private`. NULL until the locations step (304) has run — **the step must hold the row back**, same pattern as `partner_contracts`. |
| `chargingZoneId` | — | int | no | *(omit)* | **Decided: not used this iteration.** No zone concept in `laddel`; [302_target_charging_zones.sql](../../sql/302_target_charging_zones.sql) stays a placeholder. Omit the key entirely — Ampeco rejects an explicit null on create (reference-project finding). |
| `partner_id` | `partner_id` | int | no | `pm.target_partner_id` via `fc.customer_id` → `Laddel|Customer|{customer_id}` | Partner grain is `customer` (see [partner.md](partner.md)). |
| `partner_contractId` | `partner_contractId` | int | no | `pcm.target_partner_contract_id` via `Laddel|Facility|{facility_id}` | Contract grain is `facility` (see [partner_contract.md](partner_contract.md)). |
| `partner_contactId` | — | int | no | *(omit)* | New in v2.0; no source. |
| `partner_corporateBillingAsDefault` | `partner_corporateBillingAsDefault` | bool | no | `0` (Default) | Reference default. |
| `partner_accessType` | `partner_accessType` | enum | no | see rule below | **Decided (Q5/Q6)** — `is_whitelist_enabled` drives it. |
| `electricityRateId` | — | int | no | *(omit)* | No electricity rates migrated. |
| `utilityId` | — | int | no | *(omit)* | No source. |
| `installationAndMaintenanceCompanyId` | — | int | no | *(omit)* | No source; `laddel.installation` is only a facility-level grouping. |
| `noticeId` | — | int | no | *(omit)* | Notices are a separate resource, out of scope. |
| `modelId` | — | int | no | *(omit)* ⚠️ | `c.brand` exists (11 values) but no `charge-point-models` are provisioned in Ampeco. Ampeco auto-detects the model on boot. Could later map brand → `chargePointModelCreate`. |

**`partner.accessType` rule (Q5/Q6):**

```sql
CASE
    WHEN type <> 'private'          THEN NULL   -- public/personal: not applicable
    WHEN c.is_whitelist_enabled = 1 THEN 'private_view_private_use'
    ELSE                                 'private_view_public_use'
END
```

Verified counts in scope: `private_view_public_use` **5 864**,
`private_view_private_use` **199**, NULL (public) **14**.

### OCPP / network

| API field | View column | Type | Req | Source / value | Notes |
|---|---|---|:--:|---|---|
| `communicationMode` | `communicationMode` | enum | **yes**\*\* | `'direct_ocpp'` when `is_master`, else `'via_ocpp_connected_charge_point'` | Documented as "This property is required!" though not in `required[]`. Obsoletes `managedByOperator`. |
| `ocppConnectedChargePointId` | `ocppConnectedChargePointId` | int | cond. | `mst.target_charge_point_id` (self-join on `master_mapping_key`); NULL for masters | Required when `communicationMode = 'via_ocpp_connected_charge_point'`. 1 185 satellite rows. |
| `network_id` | `network_id` | string | cond. | `c.ocpp_id` (masters only) | As instructed. Required & non-empty for `direct_ocpp`. |
| `network_protocol` | `network_protocol` | enum | cond. | `'ocpp 1.6'` (Default) | **Decided (Q7): flat `ocpp 1.6` for now.** No source column in `laddel`. ⚠️ The real per-charger protocol lives in the **eMabler API** — a data extract from eMabler to backfill this is planned but **out of scope for this iteration** (see [eMabler protocol extract](#future-emabler-protocol-extract)). |
| `network_password` / `network_ip` / `network_port` | — | — | no | *(omit)* | `ip`/`port` are ocpp 1.5 SOAP only. |
| `networkType` | — | enum | no | *(omit)* | `cellular`/`ethernet`/`wlan` — no source. |
| `security_desiredProfile` | `security_desiredProfile` | int | cond. | `0` (Default) | **As instructed** — `0` = No Authentication. (The reference project used `2`.) Masters only. |
| `security_currentProfile` etc. | — | — | — | *(omit)* | Read-back fields; not ours to set. |
| `managedByOperator` | — | bool | no | *(omit)* | **Deprecated** — superseded by `communicationMode`. |

> **Satellite rows must omit** `network`, `security`, `networkType`, `capabilities`,
> `electricityRateId`, `autoStartWithoutAuthorization`, `disableAutoStartEmulation`,
> `modelId`, `enableAutoFaultRecovery`, `utilityId`, `usesRenewableEnergy`,
> `enabledRandomisedDelay`, `integratedAt`, `manufacturedAt` — per the
> `via_ocpp_connected_charge_point` contract. The view emits NULL for those columns on
> satellites and the step drops NULL keys.

### Behaviour & capabilities

| API field | View column | Type | Req | Source / value | Notes |
|---|---|---|:--:|---|---|
| `capabilities` | `capabilities` | array | no | `'["remote_start_stop_capable","meter_values","stop_transaction_on_ev_disconnect"]'` (Default) | **Confirmed.** Reference-project default; not derivable per charger. Masters only. |
| `autoStartWithoutAuthorization` | `autoStartWithoutAuthorization` | bool | no | `0` (Default) | |
| `disableAutoStartEmulation` | `disableAutoStartEmulation` | bool | no | `0` (Default) | |
| `monitoringEnabled` | `monitoringEnabled` | bool | no | `1` (Default) | **New** — replaces half of `enableAutoFaultRecovery`. |
| `autoRecoveryEnabled` | `autoRecoveryEnabled` | bool | no | `1` (Default) | **New** — replaces the other half. |
| `enableAutoFaultRecovery` | — | bool | no | *(omit)* | **Deprecated**, removal targeted v2.1 (Q2 2026). |
| `uptimeTrackingEnabled` | `uptimeTrackingEnabled` | bool | no | `1` (Default) | **Decided (Q10): enable.** Valid because every in-scope CP is commercial (`public`/`private`; 0 `personal`). ⚠️ Ampeco returns 422 unless the operator has *operational availability* enabled — verify on the target operator before the first run. |
| `usesRenewableEnergy` | — | bool | no | *(omit)* | Defaults `false`; no source. |
| `enabledRandomisedDelay` | — | bool | no | *(omit)* | Reference project hit *"Randomised delay setting is not enabled for the system"* — keep omitted. |
| `sharingCode` | — | string | no | *(omit)* | Set via the dedicated `change-sharing-code` action. |
| `calibrationLawDataAvailability` | — | enum | no | *(omit)* | Eichrecht/OICP; N/A for NO. |
| `powerSharing` | — | object | no | *(omit)* | DC cabinets only; all in-scope brands are AC. |
| `countryStationId` | — | string | no | *(omit)* | NAP reporting (DE BNetzA); N/A for NO. |

### Personal-charge-point-only blocks

All omitted this iteration (**0 personal chargers in scope**), specified for completeness:

| API field | Source / value | Notes |
|---|---|---|
| `user_id` | *(omit)* | Owner is set when the user claims the CP with the `pin`. |
| `user_automaticFirmwareUpdatesEnabled` | *(omit)* | |
| `subscription_required` | *(omit)* | Reference emitted `false`. |
| `subscription_planIds` | *(omit)* | Reference emitted `[]`. |
| `electricityCostReimbursementIntegrationId` | *(omit)* | Personal-only; ignored on public/private. |

### Tags & timestamps

| API field | View column | Type | Req | Source / value | Notes |
|---|---|---|:--:|---|---|
| `tags` | `tags` | array | no | `["Owner:Customer","Source:Laddel"]` + `"LocationType:MDU"` when `pi.priceModel = 'SUBSCRIPTION'` | Base pair matches [304_target_location.sql](../../sql/304_target_location.sql). **5 980 chargers / 418 facilities** get the MDU tag; 97 chargers (MARKUP 83 + COMMISSION 14) do not. **Q8 decided:** the same tag is added to `target.location` (304) — see [location.md](location.md#tags). |
| `integratedAt` | `integratedAt` | date-time | no | `DATE_FORMAT(c.creation_date, '%Y-%m-%dT%H:%i:%s')` | **Confirmed.** `creation_date` is NOT NULL for all 6 077 in-scope chargers. Masters only. |
| `manufacturedAt` | — | date-time | no | *(omit)* | No source. |

### Side-car calls (not part of the create payload)

| Call | Decision | Notes |
|---|---|---|
| `POST .../{chargePoint}/smart-charging` | *(skip)* ⚠️ | `laddel` has **no** electrical data — no phases, voltage, max current, phase rotation, connector type or power anywhere in the 114 source tables. The reference project's `smartcharging_*` columns have no equivalent here. Needs an enrichment table if DLM is wanted. |
| `PUT .../{chargePoint}/shared-partners` | *(skip)* | Partner grain is `customer`, and a facility maps to exactly one customer — no multi-partner sharing scenario like the reference project's Gardermoen zones. |
| `POST .../{chargePoint}/evses` | **separate step** | Grain = the same `laddel.charger` row. One EVSE per charge point under the decided grain. `status` / `active` handling belongs there. Needs its own field-mapping doc. |

---

## Decisions (2026-09-10)

| # | Question | Decision |
|:--:|---|---|
| Q1 | Grain: one CP per charger vs. one CP per `ocpp_id`? | ✅ **One CP per `laddel.charger`** (6 077 CPs; 4 892 masters + 1 185 satellites). |
| Q2 | What goes in `name`? | ✅ `'NOR' + charger_reference`, plus ` [MASTER]` / ` [SLAVE]` **only** on multi-EVSE boxes. Admin-facing, not user-visible. Unique across all 6 077 rows. |
| Q3 | `externalId`? | ✅ **Leave blank / omit.** |
| Q4 | `status`? | ✅ **Always `'enabled'`** — status is set on the EVSE instead. |
| Q5 | `partner.accessType` for private CPs? | ✅ `private_view_public_use`, but `private_view_private_use` when `is_whitelist_enabled = 1` (199 chargers). |
| Q6 | Does `is_whitelist_enabled` matter? | ✅ Yes — it is the Q5 discriminator. |
| Q7 | `network_protocol`? | ✅ Flat `'ocpp 1.6'` for now; real values to come from a future eMabler extract (below). |
| Q8 | `LocationType:MDU` on locations too? | ✅ Yes — [location.md](location.md) and `304_target_location.sql` updated. |
| Q9 | Lookup before create? | ✅ Yes — keep the reference script's `filter[networkId]` lookup-then-adopt behaviour. |
| Q10 | `uptimeTrackingEnabled`? | ✅ Enable (`true`). |
| — | `integratedAt` from `charger.creation_date`? | ✅ Confirmed. |
| — | Default `capabilities` triple? | ✅ Confirmed. |
| — | `chargingZoneId`? | ✅ Not used this iteration. |

### Future: eMabler protocol extract

`laddel` does not store the OCPP protocol version, network type, firmware or model of a
charger — that information lives in the **eMabler** platform (`facility.emabler_id`,
`installation.emabler_id`). A separate data extract from the eMabler API is planned to
backfill at least `network.protocol` (and possibly `networkType`, `modelId`,
`manufacturedAt`). **Out of scope for this iteration** — until it exists, every master
charge point is created with `network.protocol = 'ocpp 1.6'` and the extract can `PATCH`
the correct value afterwards.

## Remaining notes / risks

| Topic | Note |
|---|---|
| Two-pass step | Masters must be created before satellites so `ocppConnectedChargePointId` resolves. Implemented as two `Pass`es filtered on `communicationMode`; the loop re-reads the view between them. |
| Satellite idempotency | Satellites have no `network.id`, so they cannot be adopted by lookup — write the mapping row immediately after each create. |
| `uptimeTrackingEnabled` | Requires *operational availability* to be enabled on the target operator, else 422. **No remediation** — the step fails the row fast. Check the operator setting before the first live run. |
| Locations dependency | 2 of 441 in-scope facilities (4 and 5, the COMMISSION hotels) have no `location_mapping` row yet, so their 14 charge points are held back until the locations step covers them. |
| `modelId` | No charge-point models provisioned; `brand` has 11 values that could seed `chargePointModelCreate` later. |
| Smart charging | No electrical data in `laddel` at all — the DLM side-car call is skipped entirely. |
