# NetSuite integration — live architecture

**The droplet app never talks to NetSuite.** It holds no NetSuite credentials and makes no
outbound NetSuite calls. There is **no AI and no MCP** anywhere in the runtime. All NetSuite
communication is server-to-server between **n8n** (which signs Token-Based Auth) and a
**RESTlet** deployed in NetSuite — the same proven pattern as the vendor-credit-claims app.

> The Claude NetSuite MCP was used **only at development time**, by the engineer, to validate
> the SuiteQL against live data (see `netsuite_validation.md`). It is a browser-OAuth claude.ai
> connector, is not in the codebase, and cannot run on the droplet.

```
                 ┌─────────────── n8n (scheduler, signs OAuth1 TBA) ───────────────┐
   READS:  RESTlet(action=invoices|purchase_orders|item_receipts|…) ──rows──▶ POST /admin/ingest ──▶ Postgres cache
   WRITES: GET /admin/billing/pending ──▶ RESTlet(action=create_invoice) ──id──▶ POST /admin/billing/pushed
                 └──────────────────────────────────────────────────────────────┘
        app (FastAPI on droplet): exposes token-authed endpoints only — never calls NetSuite
        RESTlet (netsuite/3pl_restlet.js): runs the validated SuiteQL / creates draft invoices
```

## Components
- **`netsuite/3pl_restlet.js`** — deployed in NetSuite. READ actions run the validated SuiteQL
  and return ingest-shaped rows; `create_invoice` creates a **draft** invoice from billing lines.
- **`netsuite/n8n_3pl_sync.js`** — n8n Code node. Signs TBA, loops customers × entities → POSTs to
  `/admin/ingest`; then drains `/admin/billing/pending`, creates each draft invoice, posts the id back.
- **App endpoints** (token-authed via `X-Sync-Token: $SYNC_TOKEN`):
  - `POST /admin/ingest` — `{customer: slug, entity, rows[]}` → upsert (see `app/netsuite.py` for row contracts).
  - `GET  /admin/billing/pending` — billing runs queued (`ready_to_push`) with lines + customer ns ids.
  - `POST /admin/billing/pushed` — `{run_id, ns_invoice_id}` → marks the run pushed and links the invoice.

## Invoice lifecycle (why reads are authoritative)
Queue a run → n8n creates a **draft** invoice in NetSuite → a person approves/edits it there →
status moves Open→Paid/Overdue, credits may be raised. All of that lives in NetSuite, so the
portal's Invoices view is **synced from NetSuite** (read action `invoices`, incl. lines). The app
stores only `billing_run.ns_invoice_id` to link a run to its invoice — never a frozen copy.
A period already queued/pushed/invoiced is locked against re-billing.

## Deploy (one-time)
1. **Enable** SuiteCloud features: Token-Based Authentication + RESTlets.
2. **Deploy the RESTlet:** Scripting > Scripts > New, upload `netsuite/3pl_restlet.js`, Type=RESTlet,
   POST=`post`, status Released, to an integration role that can run SuiteQL and create invoices.
   Copy the External URL's `script=` / `deploy=` ids.
3. **TBA creds:** Integration record (Token-Based Auth) → consumer key/secret; Access Token → token id/secret;
   note the Account ID (realm).
4. **App env (droplet):** set `SYNC_TOKEN` to a long random secret (the app rejects ingest/billing calls
   without it). The app needs NO NetSuite credentials.
5. **n8n:** Schedule Trigger → Code node with `netsuite/n8n_3pl_sync.js`; fill the constants
   (account id, keys/token, script/deploy ids, `APP_BASE`, `SYNC_TOKEN`, each customer's NetSuite ids,
   and `CHARGE_ITEMS` mapping each charge_type → its NetSuite invoice item id).
6. **Cadence:** transactional reads hourly–4h; a weekly run also drains the billing-push queue.

## Validated NetSuite ids (from `netsuite_validation.md`)
Mova: location `49`, class `253`. Skriva: customer `10496`, vendor `10503`, location `2`, class `236`.
`inbound_shipments` read action is TODO — confirm `inboundshipment` field names against real Mova data.
