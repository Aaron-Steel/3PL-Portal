# 3PL Portal

A customer-facing visibility portal + billing automation for Macgear's new **3PL (third-party logistics) service**. Macgear receives, stores, and dispatches stock it does **not** own, on behalf of customers, and charges handling/storage fees. Everything runs through **NetSuite**.

## Status
Real app scaffolded and running locally (`./run.ps1`). NetSuite data access **validated** against
live Skriva data (`docs/netsuite_validation.md`) — all 6 views + 5 billing charges proven in SuiteQL.
Postgres schema is v1 (`db/01_schema.sql`). Billing engine implemented & verified (June demo run =
$16,765, math checks out). NetSuite integration refactored to **n8n + RESTlet** (app never calls NS).
**Only remaining go-live step:** deploy the RESTlet + n8n node and set creds/`SYNC_TOKEN` (see
`docs/netsuite_integration.md`) — all app-side code is done. First customer (Mova) stock expected **end of July 2026**.

## Build (the real app, in `app/`)
FastAPI + SQLAlchemy, `DATABASE_URL` (SQLite local / Postgres droplet). `app/main.py` (auth, customer
switcher, 6 views, billing run, admin console, token `/admin/ingest` + `/admin/billing/*`), `models.py`,
`service.py` (read views), `billing.py` (the 5 charges), `netsuite.py` (ingest_* upserts; app never calls NS),
`perms.py` (roles + view permissions), `security.py` (pbkdf2 + signed cookie), `seed.py`.
Portal UI matches `prototype/portal.html` (teal sidebar, KPI cards, chart, chips) and is responsive
(off-canvas nav drawer ≤860px). Deploy pattern = vendor-credit-claims app (Docker on n8n droplet, weekly n8n sync job).

## Auth, roles & admin (built)
Per-user login (email + pbkdf2 password, signed-cookie session). Table `app_user`. **Roles:** `admin`
(full + user/rate-card management), `internal` (all customers + billing run, no admin), `customer`
(locked to one `customer_id`, visibility views only — no billing run). **Per-user view permissions:**
default by role, overridable per user (`app_user.allowed_views` JSON; NULL = role default; see `perms.py`).
**Admin console** (`/admin/users`, `/admin/customers`, admin-only): create/edit/deactivate users (assign
role + customer + visible views + initial password), and edit per-customer rate cards — a rate change
writes a NEW effective-dated `RateCard` so past billing runs reprice correctly.
Seeded logins (dev — CHANGE): admin@macgeargroup.com/admin123,
ops@macgeargroup.com/internal123, viewer@mova.com/mova123. Auth is always on now (no shared-password mode).

## Validated NetSuite facts (2026-06-26)
Brand = NetSuite **classification** (store class id, not text). Mova class `253` (3PL - Mova) @ location
`49` (warehouse 3PL); Skriva class `236` @ location `2` (Auckland). Skriva customer `10496`, vendor
`10503`, item `S-STYCASE-WHITE`=`50101`. Picking source SO-vs-VRMA = fulfilment `entity` (customer vs
vendor); `createdfrom` is NOT selectable in SuiteQL. Open-PO test = `quantityshiprecv < quantity`.
Fulfilments emit ±qty line pairs — sum positives only. Skriva invoices are $0 product invoices, so the
**service-charge invoice is greenfield**. `inboundshipment` table exists. REST metadata catalog is 403
(permission); discover fields via `SELECT *`. Another 3PL customer already live (ClassVR) — multi-tenant confirmed.

## The business model
- Macgear does **not** buy or own the stock — it transacts it and charges a fee for receiving, storing, dispatching.
- Stock for the flagship customer (Mova) lives in a dedicated **3PL Warehouse** location (Melbourne); brand tagged **`Mova 3PL`**.
- NetSuite setup: new `3PL warehouse` location; customer record (for invoicing + $0 dispatch sales orders); supplier record (for $0 POs to receive stock); items branded `Mova 3PL` with units-per-pallet populated.

## Processes (mirror these in any data model)
- **Receiving:** $0 PO on supplier account against 3PL location → inbound shipment per container → receipt on arrival.
- **Storage:** stock on hand in 3PL location, brand `Mova 3PL`. Charged per pallet per week. Pallets = `units on hand ÷ units per pallet`.
- **Dispatching:** $0 sales order on customer record. OR Macgear buys it in: $0 VRMA to remove from 3PL inventory + physically move, then normal-price PO to the normal warehouse.
- **Billing (weekly, against customer record):** the 5 charge sources below.

## Rate card (Mova)
| Charge | Rate | Basis |
|---|---|---|
| Container unload — 40ft loose stacked | $1,500 | per container (inbound shipments received) |
| Putaway | $1.00 | per unit (item receipts vs 3PL loc, brand Mova 3PL) |
| Storage | $4.50 | per pallet / week (units on hand ÷ units/pallet) |
| Picking | $1.00 | per unit (item fulfilments — SO **and** VRMA) |
| Shipping | — | per agreed shipping rate card |

## What Mova needs to see (the 6 visibility views)
Stock on order (open POs) · Item receipts · Stock on hand · Item fulfilments (SO + VRMA) · Invoices · Rate card.

## Priorities (per brief)
1. **Visibility portal for the customer** — the priority.
2. **Automate the billing** — replace manual weekly saved searches with a draft-invoice run.
3. **Multi-tenant** — more 3PL customers are lined up; not a one-off.

## Existing reference customer
**Skriva** (NZ subsidiary) — same model at tiny scale, live in **prod + sandbox** (good for first NetSuite wiring/testing). Item `S-STYCASE-WHITE`. Difference: all transacted at $0 on the main **Auckland** warehouse, no separate 3PL location.

## Recommended approach (decided so far)
- **Build an external web app**, not an in-NetSuite Suitelet or raw Customer Center. Reason: multiple customers coming = it's a small product needing real UX + branding.
- **Fits existing Macgear stack:** FastAPI + Postgres on the n8n droplet (same pattern as the promos / vendor-credit-claims app), weekly billing job in n8n (same as birthday notifier).
- **NetSuite connection:** read via REST / **SuiteQL** (Token-Based Auth) on a schedule into a Postgres cache (the 6 views are the planned saved searches as SuiteQL). Billing automation writes **draft invoices** to the customer record via REST for approval.
- **Phasing:** (1) read-only visibility views → (2) weekly draft-invoice automation → (3) multi-tenant onboarding (Skriva + next customers, per-customer rate cards).

## Prototype
`prototype/portal.html` — self-contained clickable SPA, dummy data, all 6 views + Overview dashboard + a "Billing run" view demonstrating the automation. Customer switcher toggles Mova / Skriva to show multi-tenancy. Published as a claude.ai Artifact. To iterate: edit the file and re-publish to the same URL.

## NetSuite integration — n8n + RESTlet (app never calls NetSuite)
**The droplet app holds no NetSuite credentials and makes no NetSuite calls. No AI/MCP at runtime**
(MCP was dev-time validation only). All NS comms are server-to-server: **n8n signs TBA → RESTlet**
(`netsuite/3pl_restlet.js`), same pattern as the vendor-credit-claims app. See `docs/netsuite_integration.md`.
- **Reads:** n8n calls RESTlet (runs validated SuiteQL) → POSTs rows to token-authed `POST /admin/ingest`
  ({customer, entity, rows}); `app/netsuite.py` `ingest_*` upsert into the cache (invoices+lines, POs,
  receipts, fulfilments, stock_on_hand; inbound_shipments TODO). NetSuite is source of truth — invoices
  (status/edits/payments) come from the sync.
- **Writes:** "Queue for NetSuite" sets `billing_run.status='ready_to_push'` (no NS call). n8n polls
  `GET /admin/billing/pending`, creates the **draft** invoice via the RESTlet `create_invoice` action,
  then `POST /admin/billing/pushed` ({run_id, ns_invoice_id}) → status `pushed`. Next read-sync pulls the
  real invoice; the run links to it via `ns_invoice_id`. Statuses: draft→ready_to_push→pushed→invoiced.
- **Re-billing guard:** a period already queued/pushed/invoiced can't be re-saved or re-queued.
- Customers drill the Invoices list → per-invoice charge-line detail (`/c/{slug}/invoice/{id}`, customer-scoped).
- Artifacts: `netsuite/3pl_restlet.js`, `netsuite/n8n_3pl_sync.js`. App needs only env `SYNC_TOKEN`.

## Notes
- Aaron (aaron@macgeargroup.com) is the owner. Mid warehouse relocation in Melbourne; in Bali 9–20 July 2026.
- NetSuite MCP is available in this environment (`mcp__claude_ai_NetSuite__authenticate`) for live data once ready.
