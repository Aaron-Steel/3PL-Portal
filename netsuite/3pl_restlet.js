/**
 * 3PL Portal RESTlet — the ONLY thing that talks to NetSuite for the portal.
 *
 * n8n (token-based auth) POSTs an {action, ...} body. READ actions run the validated
 * SuiteQL (docs/netsuite_validation.md) and return rows shaped for the app's /admin/ingest.
 * The WRITE action creates a DRAFT invoice from billing lines and returns its id.
 *
 * No app/AI/MCP involvement — pure server-to-server SuiteScript.
 *
 * Deploy: Customization > Scripting > Scripts > New, upload this file, Type=RESTlet,
 * POST function = `post`, Deploy as Released to an integration role with the needed
 * permissions (run SuiteQL, create invoices). Use the deployment's External URL from n8n.
 *
 * @NApiVersion 2.1
 * @NScriptType Restlet
 */
define(['N/query', 'N/record'], function (query, record) {

  function runSuiteQL(sql) {
    var rows = [];
    var more = true, page = 0;
    while (more) {
      var res = query.runSuiteQLPaged({ query: sql, pageSize: 1000 });
      res.pageRanges.forEach(function (r) {
        res.fetch({ index: r.index }).data.asMappedResults().forEach(function (m) { rows.push(m); });
      });
      more = false; // runSuiteQLPaged already returns all pages via pageRanges
    }
    return rows;
  }

  // group flat header/line rows (keyed by tranid id) into {header..., lines:[...]}
  function group(flat, idKey, header, line) {
    var byId = {}, order = [];
    flat.forEach(function (r) {
      var id = String(r[idKey]);
      if (!byId[id]) { byId[id] = header(r); byId[id].lines = []; order.push(id); }
      var ln = line(r); if (ln) byId[id].lines.push(ln);
    });
    return order.map(function (id) { return byId[id]; });
  }

  // ---- READ actions (params are NetSuite internal ids from the app's customer record) ----
  function invoices(p) {
    var heads = runSuiteQL(
      "SELECT id, tranid, trandate, BUILTIN.DF(status) status, foreigntotal total " +
      "FROM transaction WHERE type='CustInvc' AND entity=" + Number(p.ns_customer_id));
    return heads.map(function (h) {
      var lines = runSuiteQL(
        "SELECT BUILTIN.DF(item) item_name, memo, quantity, rate, netamount " +
        "FROM transactionline WHERE transaction=" + Number(h.id) +
        " AND mainline='F' AND taxline='F' ORDER BY linesequencenumber");
      return {
        ns_invoice_id: String(h.id), tranid: h.tranid, trandate: h.trandate,
        status: h.status, total: h.total,
        lines: lines.map(function (l) {
          return { description: l.memo || l.item_name, qty: l.quantity,
                   rate: l.rate, amount: l.netamount };
        })
      };
    });
  }

  function purchaseOrders(p) {
    var flat = runSuiteQL(
      "SELECT t.id, t.tranid, t.trandate, BUILTIN.DF(t.status) status, tl.item, " +
      "tl.quantity ordered, tl.quantityshiprecv received FROM transaction t " +
      "JOIN transactionline tl ON tl.transaction=t.id WHERE t.type='PurchOrd' AND t.entity=" +
      Number(p.ns_supplier_id) + " AND tl.location=" + Number(p.ns_location_id) +
      " AND tl.mainline='F' AND tl.taxline='F' AND tl.quantityshiprecv < tl.quantity");
    return group(flat, 'id',
      function (r) { return { ns_po_id: String(r.id), tranid: r.tranid, trandate: r.trandate, status: r.status }; },
      function (r) { return { ns_item_id: String(r.item), qty_ordered: r.ordered, qty_received: r.received }; });
  }

  function itemReceipts(p) {
    // Scope by the ITEM's brand class (reliably set on the item; a manually-keyed receipt line
    // often has no class) so shared locations don't leak other customers' receipts.
    var flat = runSuiteQL(
      "SELECT t.id, t.tranid, t.trandate, tl.item, tl.quantity FROM transaction t " +
      "JOIN transactionline tl ON tl.transaction=t.id JOIN item i ON i.id=tl.item " +
      "WHERE t.type='ItemRcpt' AND tl.location=" + Number(p.ns_location_id) +
      " AND i.class=" + Number(p.ns_class_id) +
      " AND tl.mainline='F' AND tl.taxline='F' AND t.trandate >= " + sinceExpr(p.since));
    return group(flat, 'id',
      function (r) { return { ns_receipt_id: String(r.id), tranid: r.tranid, trandate: r.trandate }; },
      function (r) { return { ns_item_id: String(r.item), qty: r.quantity }; });
  }

  function itemFulfilments(p) {
    var flat = runSuiteQL(
      "SELECT t.id, t.tranid, t.trandate, t.entity, tl.item, tl.quantity FROM transaction t " +
      "JOIN transactionline tl ON tl.transaction=t.id JOIN item i ON i.id=tl.item " +
      "WHERE t.type='ItemShip' AND t.entity IN (" +
      Number(p.ns_customer_id) + "," + Number(p.ns_supplier_id) + ") AND i.class=" +
      Number(p.ns_class_id) + " AND tl.mainline='F' AND tl.taxline='F' AND tl.quantity > 0 " +
      "AND t.trandate >= " + sinceExpr(p.since));
    var custId = String(p.ns_customer_id);
    return group(flat, 'id',
      function (r) { return { ns_fulfilment_id: String(r.id), tranid: r.tranid, trandate: r.trandate,
                              source_type: String(r.entity) === custId ? 'SO' : 'VRMA' }; },
      function (r) { return { ns_item_id: String(r.item), qty: r.quantity }; });
  }

  function stockOnHand(p) {
    // inventorybalance has multiple rows per item (status/bin); sum to one net qty/item. Filter by
    // the item's brand class so a shared location (e.g. Skriva @ Auckland) returns only this
    // customer's stock, not the whole warehouse.
    var flat = runSuiteQL(
      "SELECT ib.item, SUM(ib.quantityonhand) qty FROM inventorybalance ib " +
      "JOIN item i ON i.id=ib.item WHERE ib.location=" + Number(p.ns_location_id) +
      " AND i.class=" + Number(p.ns_class_id) + " GROUP BY ib.item");
    return flat.map(function (r) { return { ns_item_id: String(r.item), qty_on_hand: r.qty }; });
  }

  // item master for the customer's brand (class) — itemid = SKU shown in the portal; the portal
  // resolves fact rows' internal ids to these. units_per_pallet is a custom item field — pass its
  // field id in p.upp_field to include it (e.g. 'custitem_units_per_pallet'), else it stays null.
  function items(p) {
    // itemid = SKU; displayname = the readable name shown in the portal (salesdescription is not a
    // SuiteQL item column; description is usually null). units_per_pallet via optional custom field.
    var cols = "id, itemid, displayname, description";
    if (p.upp_field) cols += ", " + String(p.upp_field).replace(/[^a-z0-9_]/gi, '') + " upp";
    var flat = runSuiteQL("SELECT " + cols + " FROM item WHERE class=" + Number(p.ns_class_id));
    return flat.map(function (r) {
      return { ns_item_id: String(r.id), sku: r.itemid,
               description: r.displayname || r.description || null,
               units_per_pallet: (r.upp === undefined ? null : r.upp) };
    });
  }

  function sinceExpr(since) {
    // since = 'YYYY-MM-DD' (default 2020-01-01); SuiteQL date literal
    var d = (since && /^\d{4}-\d{2}-\d{2}$/.test(since)) ? since : '2020-01-01';
    return "TO_DATE('" + d + "','YYYY-MM-DD')";
  }

   // ---- WRITE action: create a DRAFT invoice from billing lines ----
  function createInvoice(p) {
    var rec = record.create({ type: record.Type.INVOICE, isDynamic: true });
    rec.setValue({ fieldId: 'entity', value: Number(p.ns_customer_id) });
    if (p.ns_subsidiary_id) rec.setValue({ fieldId: 'subsidiary', value: Number(p.ns_subsidiary_id) });
    // Location is mandatory on transactions in this account — set it on the header (cascades to
    // lines) and on each line for accounts that require it line-level.
    if (p.ns_location_id) rec.setValue({ fieldId: 'location', value: Number(p.ns_location_id) });
    if (p.memo) rec.setValue({ fieldId: 'memo', value: p.memo });
    (p.lines || []).forEach(function (l) {
      rec.selectNewLine({ sublistId: 'item' });
      rec.setCurrentSublistValue({ sublistId: 'item', fieldId: 'item', value: Number(l.item_id) });
      rec.setCurrentSublistValue({ sublistId: 'item', fieldId: 'quantity', value: Number(l.qty) });
      rec.setCurrentSublistValue({ sublistId: 'item', fieldId: 'rate', value: Number(l.rate) });
      if (l.description) rec.setCurrentSublistValue({ sublistId: 'item', fieldId: 'description', value: l.description });
      if (p.ns_location_id) rec.setCurrentSublistValue({ sublistId: 'item', fieldId: 'location', value: Number(p.ns_location_id) });
      rec.commitLine({ sublistId: 'item' });
    });
    var id = rec.save({ enableSourcing: true, ignoreMandatoryFields: false });
    return { ns_invoice_id: String(id) };
  }

  var ACTIONS = {
    items: items,
    invoices: invoices, purchase_orders: purchaseOrders, item_receipts: itemReceipts,
    item_fulfilments: itemFulfilments, stock_on_hand: stockOnHand,
    create_invoice: createInvoice
    // inbound_shipments: TODO — confirm inboundshipment field names against real Mova data.
  };

  function post(body) {
    try {
      var action = body && body.action;
      if (!ACTIONS[action]) return { error: 'unknown action: ' + action };
      return { ok: true, data: ACTIONS[action](body) };
    } catch (e) {
      return { error: (e && e.name) || 'error', message: (e && e.message) || String(e) };
    }
  }

  return { post: post };
});
