// n8n Code node — 3PL Portal NetSuite sync (mode: "Run Once for All Items").
// Signs NetSuite Token-Based Auth itself (OAuth1, HMAC-SHA256), calls the RESTlet
// (netsuite/3pl_restlet.js), and talks to the app's token-authed endpoints. No app/AI/MCP
// touches NetSuite — this node + the RESTlet are the only NetSuite communication.
//
// Wire as:  Schedule Trigger (e.g. hourly / weekly)  ->  this Code node.

const crypto = require('crypto');

// ---------- fill these in (or use $env.* on the n8n container) ----------
const ACCOUNT_ID      = 'REPLACE_ACCOUNT_ID';        // e.g. 1234567 or 1234567_SB1
const CONSUMER_KEY    = 'REPLACE_CONSUMER_KEY';
const CONSUMER_SECRET = 'REPLACE_CONSUMER_SECRET';
const TOKEN_ID        = 'REPLACE_TOKEN_ID';
const TOKEN_SECRET    = 'REPLACE_TOKEN_SECRET';
const RESTLET_SCRIPT  = 'REPLACE_SCRIPT_ID';         // "script=" in the RESTlet deploy URL
const RESTLET_DEPLOY  = 'REPLACE_DEPLOY_ID';         // "deploy=" in the RESTlet deploy URL
const APP_BASE        = 'http://threepl:8000';       // app on the shared Docker network
const SYNC_TOKEN      = 'REPLACE_SYNC_TOKEN';        // == app SYNC_TOKEN env
const SINCE           = '2025-01-01';                // incremental floor for dated reads

// Customers to sync (NetSuite internal ids from each app customer record).
const CUSTOMERS = [
  { slug: 'mova', ns_customer_id: 'REPLACE', ns_supplier_id: 'REPLACE',
    ns_location_id: '49', ns_class_id: '253', ns_subsidiary_id: '2' },
  { slug: 'skriva', ns_customer_id: '10496', ns_supplier_id: '10503',
    ns_location_id: '2', ns_class_id: '236', ns_subsidiary_id: '3' },
];
const READ_ENTITIES = ['invoices', 'purchase_orders', 'item_receipts',
                       'item_fulfilments', 'stock_on_hand'];
// charge_type -> NetSuite item internalid (for draft-invoice lines on push)
const CHARGE_ITEMS = { container_unload: 'REPLACE', putaway: 'REPLACE',
                       storage: 'REPLACE', picking_so: 'REPLACE', picking_vrma: 'REPLACE' };
// ------------------------------------------------------------------------

const host = ACCOUNT_ID.toLowerCase().replace(/_/g, '-');
const RESTLET_BASE = `https://${host}.restlets.api.netsuite.com/app/site/hosting/restlet.nl`;
const helpers = this.helpers;
const pct = (s) => encodeURIComponent(String(s)).replace(/[!*'()]/g,
  (c) => '%' + c.charCodeAt(0).toString(16).toUpperCase());

function authHeader(method, baseUrl, queryParams) {
  const oauth = {
    oauth_consumer_key: CONSUMER_KEY, oauth_token: TOKEN_ID,
    oauth_signature_method: 'HMAC-SHA256',
    oauth_timestamp: Math.floor(Date.now() / 1000).toString(),
    oauth_nonce: crypto.randomBytes(16).toString('hex'), oauth_version: '1.0',
  };
  const all = Object.assign({}, queryParams, oauth);
  const paramStr = Object.keys(all).sort().map((k) => pct(k) + '=' + pct(all[k])).join('&');
  const base = [method.toUpperCase(), pct(baseUrl), pct(paramStr)].join('&');
  const signingKey = pct(CONSUMER_SECRET) + '&' + pct(TOKEN_SECRET);
  const signature = crypto.createHmac('sha256', signingKey).update(base).digest('base64');
  const headerParams = Object.assign({}, oauth, { oauth_signature: signature });
  return 'OAuth realm="' + ACCOUNT_ID + '", ' + Object.keys(headerParams).sort()
    .map((k) => pct(k) + '="' + pct(headerParams[k]) + '"').join(', ');
}

// Call the RESTlet (POST + JSON body; only script/deploy are signed query params).
async function restlet(body) {
  const q = { script: RESTLET_SCRIPT, deploy: RESTLET_DEPLOY };
  const url = RESTLET_BASE + '?' + Object.keys(q).map((k) => pct(k) + '=' + pct(q[k])).join('&');
  const resp = await helpers.httpRequest({
    method: 'POST', url, body,
    headers: { Authorization: authHeader('POST', RESTLET_BASE, q), 'Content-Type': 'application/json' },
    json: true, returnFullResponse: true, ignoreHttpStatusErrors: true,
  });
  if (resp.statusCode !== 200 || !resp.body || resp.body.error) {
    throw new Error('RESTlet ' + body.action + ': ' + JSON.stringify(resp.body));
  }
  return resp.body.data;
}

const appHeaders = { 'X-Sync-Token': SYNC_TOKEN, 'Content-Type': 'application/json' };
const out = [];

// 1) READS: pull each entity per customer and push to /admin/ingest
for (const c of CUSTOMERS) {
  for (const entity of READ_ENTITIES) {
    try {
      const rows = await restlet(Object.assign({ action: entity, since: SINCE }, c));
      const r = await helpers.httpRequest({
        method: 'POST', url: `${APP_BASE}/admin/ingest`, headers: appHeaders,
        body: { customer: c.slug, entity, rows }, json: true });
      out.push({ json: r });
    } catch (e) {
      out.push({ json: { step: 'read', customer: c.slug, entity, error: String(e.message || e) } });
    }
  }
}

// 2) WRITES: create a draft invoice for each queued billing run, then report it back
try {
  const pending = (await helpers.httpRequest({
    method: 'GET', url: `${APP_BASE}/admin/billing/pending`, headers: appHeaders, json: true })).pending || [];
  for (const run of pending) {
    try {
      const lines = run.lines.map((l) => ({ item_id: CHARGE_ITEMS[l.charge_type],
        description: l.description, qty: l.qty, rate: l.rate }));
      const res = await restlet({ action: 'create_invoice', ns_customer_id: run.ns_customer_id,
        ns_subsidiary_id: run.ns_subsidiary_id,
        memo: `3PL charges ${run.period_start}–${run.period_end}`, lines });
      const back = await helpers.httpRequest({
        method: 'POST', url: `${APP_BASE}/admin/billing/pushed`, headers: appHeaders,
        body: { run_id: run.run_id, ns_invoice_id: res.ns_invoice_id }, json: true });
      out.push({ json: back });
    } catch (e) {
      out.push({ json: { step: 'push', run_id: run.run_id, error: String(e.message || e) } });
    }
  }
} catch (e) {
  out.push({ json: { step: 'pending', error: String(e.message || e) } });
}

return out;
