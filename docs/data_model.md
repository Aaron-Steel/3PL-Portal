# 3PL Portal — Data Model & NetSuite Sync Spec (v0)

> **Status: v0 — drafted from the brief, NOT yet validated against live NetSuite.**
> Field/record names below are best-guess. Once the NetSuite MCP is connected we validate every
> SuiteQL query against live **Skriva** data and correct names here, then promote to v1.

## Design principles

1. **NetSuite is the system of record.** Postgres is a **read cache** that powers the portal and feeds
   the billing run. We never write operational stock data back to NetSuite from the portal — the only
   write-back is **draft invoices** (phase 2), created via REST against the customer record.
2. **Multi-tenant from day one.** Every fact row is keyed by `customer_id`. Mova and Skriva are just two
   rows in `customer`. New 3PL customers = new `customer` + `rate_card` rows, no schema change.
3. **Mirror, don't transform.** Cache tables mirror the shape of the NetSuite transactions (PO, inbound
   shipment, item receipt, fulfilment, invoice) plus a stock-on-hand snapshot. The 6 portal views and the
   5 billing charges are all derived from these.
4. **Idempotent sync.** Every sync upserts on the NetSuite internal id. Re-running a sync never duplicates.

## The two customer shapes (why the model must flex)

| | **Mova** (target) | **Skriva** (reference, live now) |
|---|---|---|
| Stock location | dedicated `3PL Warehouse` (Melbourne) | main `Auckland` warehouse |
| Brand tag | `Mova 3PL` | (TBD — confirm in NetSuite) |
| Item example | (TBD) | `S-STYCASE-WHITE` |
| Separate 3PL location? | **yes** | **no** ($0 on main warehouse) |

The cache must isolate a customer's stock by **(location AND/OR brand)**, because Skriva proves you can't
rely on a dedicated location alone. So `customer` carries **both** an optional `netsuite_location_id` and a
`brand_tag`, and the sync filter for each customer is `location = X` **and/or** `brand = Y` — whichever
uniquely identifies that customer's 3PL stock. **(Validate the exact filter per customer in NetSuite.)**

## The 6 visibility views → source mapping

| # | Portal view | Cache table(s) | NetSuite source | Filter |
|---|---|---|---|---|
| 1 | Stock on order | `purchase_order` + `po_line` | open Purchase Orders | supplier = customer's supplier, location = 3PL loc, status = open |
| 2 | Item receipts | `item_receipt` + `item_receipt_line` | Item Receipts | location = 3PL loc, brand = brand_tag |
| 3 | Stock on hand | `stock_on_hand` (snapshot) | inventory balance | location = 3PL loc, brand = brand_tag |
| 4 | Item fulfilments | `item_fulfilment` + `..._line` | Item Fulfilments | from SO on customer **OR** from VRMA on supplier |
| 5 | Invoices | `invoice` + `invoice_line` | Invoices | customer = customer record |
| 6 | Rate card | `rate_card` + `rate_card_line` | (config, not NetSuite) | per customer, effective-dated |

## The 5 billing charges → source mapping

| Charge | Rate (Mova) | Basis | Derived from |
|---|---|---|---|
| Container unload (40ft loose) | $1,500 | per container | count of `inbound_shipment` **received** in period |
| Putaway | $1.00 | per unit | sum of `item_receipt_line.qty` in period |
| Storage | $4.50 | per pallet/week | **avg daily pallets** over the period × weeks (`ceil(units_on_hand / units_per_pallet)` totalled per snapshot day, averaged) |
| Picking — SO | $1.00 | per unit | sum of `item_fulfilment_line.qty` where source = SO |
| Picking — VRMA | $1.00 | per unit | sum of `item_fulfilment_line.qty` where source = VRMA |
| Shipping | per shipping rate card | — | out of scope for v1 auto-billing |

> **Storage is the tricky one.** "Per pallet per week" needs a defensible weekly pallet figure.
> **Current decision (2026-06-27):** now that SOH refreshes every ~15 min and persists one row per
> day, bill the **average of the daily pallet totals** across the billing week × the number of weeks
> the period spans (`billing.py`). This is the "avg daily pallets" model — more accurate than a single
> weekly reading and robust to how often we snapshot. With only one snapshot in the period it degrades
> to "that reading held all week" (the original v0 weekly-snapshot behaviour). **Critical:** never *sum*
> every snapshot — at daily/intraday cadence that overcharges ~7×.

## Sync design

- **Mechanism:** NetSuite REST/SuiteQL via Token-Based Auth (TBA), pulled on a schedule into Postgres.
- **Cadence (current):** two n8n lanes off the same Code node (`netsuite/n8n_3pl_sync.js`, mode-driven).
  - **Fast lane — `stock_on_hand` only, every 15 min** (`mode:"soh"`, no billing writes): keeps the portal's
    SOH view near-live. Today's row is overwritten in place; the view shows a "● live · updated N min ago" stamp.
  - **Full lane — all 6 entities + draft-invoice writes, daily + the weekly billing window** (`mode:"full"`):
    transactional tables (PO, receipts, fulfilments, invoices, inbound shipments) and the billing push.
    Daily SOH rows accumulate as history that the storage charge averages.
- **Runner:** n8n scheduled workflow on the droplet (same pattern as the birthday notifier), calling the
  app's sync endpoints, **or** an in-app APScheduler job. Decide at scaffold time.
- **Watermark:** each sync stores `last_synced_at` + last seen `lastmodifieddate` in `sync_log`; incremental
  pulls use `WHERE lastmodifieddate > watermark` where the record type supports it; full refresh for SOH snapshot.
- **Idempotency:** upsert on `netsuite_id`. Lines replaced wholesale per parent transaction on each sync.

## Open questions to resolve against live NetSuite

1. Exact record/field names in SuiteQL for: inbound shipment received status & date, item receipt → brand,
   inventory balance by location+brand, fulfilment source (SO vs VRMA) discriminator.
2. How "brand" is stored on the item (custom field id? `class`/`custitem_*`?) — drives the brand filter.
3. Skriva's actual brand tag + whether its stock is isolable without a dedicated location.
4. Units-per-pallet field id on the item record.
5. Container = one inbound shipment? Confirm 1:1 so "per container" = count of inbound shipments.
