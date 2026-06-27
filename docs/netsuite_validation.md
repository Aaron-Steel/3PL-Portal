# NetSuite Validation — proven against live data (2026-06-26)

All queries below were **run successfully against the live NetSuite account** via SuiteQL (Skriva
reference customer). This is the de-risked basis for the sync layer. Mova-specific ids are marked TODO
until Mova items/transactions exist (~end Jul 2026).

## Access notes
- `ns_runCustomSuiteQL` works. The **metadata catalog endpoint is permission-blocked** (HTTP 403,
  "REST Web Services" feature) — discover fields with `SELECT *` / `SELECT` probes instead.
- `BUILTIN.DF(col)` resolves an internal id to its display label — use freely in SELECT.
- **`createdfrom` is NOT selectable** in SuiteQL here (throws "unexpected SuiteScript error"). Do not use it.
- Transient `HTTP 502 Bad gateway` happens — just retry after a moment.
- `SELECT *` is supported on `item`; `WITH`/CTEs are not; string concat is `||`; dates via `TO_DATE`.

## Resolved internal ids

| Thing | Id | Notes |
|---|---|---|
| Subsidiary — MacGear AU | `2` | Mova lives here |
| Subsidiary — MacGear NZ | `3` | Skriva lives here |
| Location — AU2 Melbourne Warehouse | `34` | parent of the 3PL location |
| **Location — warehouse 3PL** | **`49`** | `AU2 – Melbourne Warehouse : warehouse 3PL` → **Mova's 3PL location** |
| Location — NZ2 Auckland | `2` | Skriva's location (no dedicated 3PL loc) |
| Location — ClassVR 3PL | `22`, `29` | **another existing 3PL customer** — confirms multi-tenant |
| Class/brand — SKRIVA STYLUS | `236` | Skriva's brand tag |
| **Class/brand — 3PL - Mova** | **`253`** | Mova's brand tag (also `231` "3PL", `237` "MOVA") |
| Skriva customer | `10496` | entityid `03191` "Skriva Stylus" |
| Skriva vendor | `10503` | entityid `V01157` |
| Skriva item (white) | `50101` | `S-STYCASE-WHITE`; (blue = `38693`) |

> **Brand is the NetSuite `classification`, not a free-text field.** Store the class **id** per customer,
> not a string. The stock-isolation filter per customer = `location = X AND class = Y`.

## Item record fields (from `SELECT * FROM item WHERE id=50101`)
- `class` = brand (236). `subsidiary`, `totalquantityonhand` present on the item.
- **Units-per-pallet candidate: `custitem_pallet_quantity`** (null on Skriva — Skriva isn't palletised).
  Also `custitem_pallet_layer_quantity` and `custitem_mcg_item_master_qty` (="120", master carton qty).
  **TODO: confirm which field Mova populates** once Mova items exist; the brief says units/pallet is set on Mova items.

## Transaction taxonomy (validated)
Customer (10496): `SalesOrd`, `ItemShip` (fulfilment), `CustInvc`. Vendor (10503): `PurchOrd`, `ItemRcpt`, `VendBill`.
VRMA = type **`VendAuth`** (none in Skriva history — it's the Mova "Macgear buys-in" path).

`transactionline` exposes everything needed: `item`, `quantity`, `quantityshiprecv`, `location`, `class`,
`netamount`, `rate`, `linesequencenumber`. Filter item lines with `mainline='F' AND taxline='F'`.

### Important data nuances
- **Item fulfilments emit paired +qty / −qty lines** for the same item (item line vs inventory-impact line).
  For pick-fee counting **sum positive quantities only** — do NOT net them (would give 0).
- **Open PO test:** PO line where `quantityshiprecv < quantity`. Status-independent and reliable.
- **Picking source (SO vs VRMA):** discriminate by the fulfilment's `entity` — customer id ⇒ SO pick,
  vendor id ⇒ VRMA pick. (Can't use `createdfrom`.)
- **Skriva invoices are $0 product invoices, not service charges.** Skriva isn't billed 3PL fees in NetSuite
  today. ⇒ **the service-charge invoice is greenfield**; our billing run defines the charge lines from scratch.

## Validated query library (the sync queries)

> Parameterise `:loc` (3PL location), `:class` (brand), `:cust` (customer id), `:vend` (vendor id),
> `:from`/`:to` (period). Skriva test values: loc=2, class=236, cust=10496, vend=10503.

**View 1 — Stock on order (open POs):**
```sql
SELECT t.id, t.tranid, t.trandate, tl.item, BUILTIN.DF(tl.item) item_name,
       tl.quantity ordered, tl.quantityshiprecv received,
       (tl.quantity - tl.quantityshiprecv) outstanding
FROM transaction t JOIN transactionline tl ON tl.transaction = t.id
WHERE t.type='PurchOrd' AND t.entity=:vend AND tl.location=:loc
  AND tl.mainline='F' AND tl.taxline='F' AND tl.quantityshiprecv < tl.quantity
```

**View 2 / Putaway charge — item receipts:**
```sql
SELECT t.id, t.tranid, t.trandate, tl.item, BUILTIN.DF(tl.item) item_name, tl.quantity
FROM transaction t JOIN transactionline tl ON tl.transaction = t.id
WHERE t.type='ItemRcpt' AND tl.location=:loc AND tl.class=:class
  AND tl.mainline='F' AND tl.taxline='F' AND t.trandate BETWEEN :from AND :to
```

**View 3 / Storage charge — stock on hand:**
```sql
SELECT item, BUILTIN.DF(item) item_name, quantityonhand, quantityavailable
FROM inventorybalance WHERE location=:loc AND item IN (/* customer's items */)
-- pallets = CEIL(quantityonhand / units_per_pallet); snapshot weekly for billing
```

**View 4 / Picking charge — fulfilments (ASSET line, ABS qty):**
> CORRECTION (2026-06-27): the original "sum positives only" was wrong — it dropped every VRMA.
> A customer SO fulfilment emits a +qty COGS / −qty ASSET line pair, but a VRMA fulfilment (ship
> back to the supplier) emits a SINGLE **negative** ASSET line with no positive counterpart, so
> `tl.quantity > 0` returned nothing for VRMAs even though stock left NetSuite. The ASSET line is
> the real inventory movement in both cases (negative = leaving), so filter to it and take ABS().
```sql
SELECT t.id, t.tranid, t.trandate, t.entity,
       CASE WHEN t.entity=:cust THEN 'SO' ELSE 'VRMA' END source,
       tl.item, BUILTIN.DF(tl.item) item_name, ABS(tl.quantity) qty
FROM transaction t JOIN transactionline tl ON tl.transaction = t.id
JOIN item i ON i.id = tl.item
WHERE t.type='ItemShip' AND t.entity IN (:cust, :vend) AND i.class=:class
  AND tl.mainline='F' AND tl.taxline='F'
  AND tl.accountinglinetype='ASSET' AND tl.quantity IS NOT NULL AND tl.quantity <> 0
  AND t.trandate BETWEEN :from AND :to
```

**View 5 — invoices on the customer:**
```sql
SELECT t.id, t.tranid, t.trandate, BUILTIN.DF(t.status) status, t.foreigntotal total
FROM transaction t WHERE t.type='CustInvc' AND t.entity=:cust ORDER BY t.trandate DESC
```

**Container-unload charge — inbound shipments received in period:**
```sql
-- inboundshipment table confirmed (87 rows acct-wide). TODO: confirm customer link + received-date/status
-- field names against a real Mova inbound shipment; Skriva has none to test against.
SELECT * FROM inboundshipment WHERE ... -- validate fields when Mova data exists
```

## Remaining TODO (need Mova data, ~end Jul 2026)
1. Confirm Mova item `custitem_pallet_quantity` is the units/pallet field and is populated.
2. `inboundshipment` field names: customer/vendor link, container type, received date & status.
3. First real VRMA fulfilment to confirm the entity-based SO/VRMA discriminator on Mova.
4. Confirm Mova's exact class id on items is `253` (vs `237`/`231`).
