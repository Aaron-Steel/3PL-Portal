"""Read-side helpers: turn cache tables into the 6 portal views + overview.

Items are resolved to SKUs via a per-customer ns_item_id -> sku map so the portal shows
human SKUs, not NetSuite internal ids. The "current billing week" is anchored to the latest
activity in the cache (not the wall clock) so the demo always shows populated numbers.
"""
import math
from datetime import date, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .billing import active_rate_card, compute_billing
from .models import (Customer, InboundShipment, Item, ItemFulfilment, ItemReceipt,
                     Invoice, InvoiceLine, PurchaseOrder, StockOnHand)


def item_map(db: Session, customer_id: int) -> dict[str, str]:
    rows = db.scalars(select(Item).where(Item.customer_id == customer_id)).all()
    return {i.ns_item_id: i.sku for i in rows}


def item_names(db: Session, customer_id: int) -> dict[str, str]:
    rows = db.scalars(select(Item).where(Item.customer_id == customer_id)).all()
    return {i.ns_item_id: (i.description or i.sku) for i in rows}


def storage_rate(db: Session, customer_id: int) -> float:
    card = active_rate_card(db, customer_id, date.today())
    if not card:
        return 0.0
    for l in card.lines:
        if l.charge_type == "storage":
            return float(l.rate)
    return 0.0


# --- the 6 views -------------------------------------------------------------
def stock_on_order(db: Session, customer_id: int, imap: dict) -> list[dict]:
    pos = db.scalars(
        select(PurchaseOrder).where(PurchaseOrder.customer_id == customer_id,
                                    PurchaseOrder.status != "closed")
        .order_by(PurchaseOrder.trandate.desc())).all()
    out = []
    for po in pos:
        for l in po.lines:
            outstanding = float(l.qty_ordered or 0) - float(l.qty_received or 0)
            if outstanding <= 0:
                continue
            out.append({"tranid": po.tranid, "trandate": po.trandate, "status": po.status,
                        "sku": imap.get(l.ns_item_id, l.ns_item_id),
                        "ordered": float(l.qty_ordered or 0),
                        "received": float(l.qty_received or 0),
                        "outstanding": outstanding, "expected": l.expected_date})
    return out


def item_receipts(db: Session, customer_id: int, imap: dict,
                  names: dict | None = None) -> list[dict]:
    names = names or {}
    recs = db.scalars(
        select(ItemReceipt).where(ItemReceipt.customer_id == customer_id)
        .order_by(ItemReceipt.trandate.desc())).all()
    out = []
    for r in recs:
        for l in r.lines:
            out.append({"tranid": r.tranid, "trandate": r.trandate,
                        "shipment": r.ns_inbound_shipment, "po": r.po_tranid,
                        "sku": imap.get(l.ns_item_id, l.ns_item_id),
                        "name": names.get(l.ns_item_id, ""), "qty": float(l.qty)})
    return out


def stock_on_hand(db: Session, customer_id: int, imap: dict,
                  names: dict | None = None) -> list[dict]:
    """Latest snapshot per item, with storage/week derived from the rate card.
    The snapshot is refreshed in place every ~15 min, so rows carry synced_at
    (use soh_synced_at() for the single "live as at" time shown in the portal).
    Items currently at zero on hand are dropped from the view."""
    rate = storage_rate(db, customer_id)
    names = names or {}
    latest = db.scalar(
        select(StockOnHand.snapshot_date).where(StockOnHand.customer_id == customer_id)
        .order_by(StockOnHand.snapshot_date.desc()).limit(1))
    if latest is None:
        return []
    rows = db.scalars(
        select(StockOnHand).where(StockOnHand.customer_id == customer_id,
                                  StockOnHand.snapshot_date == latest)).all()
    out = []
    for s in rows:
        if float(s.qty_on_hand or 0) == 0:      # zeroed-out (shipped to nil) — hide from view
            continue
        pallets = (float(s.pallets) if s.pallets is not None else
                   (math.ceil(float(s.qty_on_hand) / s.units_per_pallet)
                    if s.units_per_pallet else 0))
        out.append({"sku": imap.get(s.ns_item_id, s.ns_item_id),
                    "name": names.get(s.ns_item_id, ""),
                    "qty_on_hand": float(s.qty_on_hand),
                    "units_per_pallet": s.units_per_pallet, "pallets": pallets,
                    "storage_per_week": round(pallets * rate, 2),
                    "snapshot_date": s.snapshot_date, "synced_at": s.synced_at})
    return out


def soh_synced_at(db: Session, customer_id: int):
    """The 'live as at' time for stock on hand — most recent synced_at on the latest
    snapshot day. Falls back to the snapshot date if synced_at was never written."""
    latest = db.scalar(
        select(StockOnHand.snapshot_date).where(StockOnHand.customer_id == customer_id)
        .order_by(StockOnHand.snapshot_date.desc()).limit(1))
    if latest is None:
        return None
    return db.scalar(
        select(func.max(StockOnHand.synced_at)).where(
            StockOnHand.customer_id == customer_id,
            StockOnHand.snapshot_date == latest)) or latest


def fulfilments(db: Session, customer_id: int, imap: dict) -> list[dict]:
    fs = db.scalars(
        select(ItemFulfilment).where(ItemFulfilment.customer_id == customer_id)
        .order_by(ItemFulfilment.trandate.desc())).all()
    out = []
    for f in fs:
        for l in f.lines:
            out.append({"tranid": f.tranid, "trandate": f.trandate,
                        "source": f.source_type, "ref": f.ns_source_id,
                        "sku": imap.get(l.ns_item_id, l.ns_item_id), "qty": float(l.qty)})
    return out


def invoices(db: Session, customer_id: int) -> list[dict]:
    rows = db.scalars(
        select(Invoice).where(Invoice.customer_id == customer_id)
        .order_by(Invoice.trandate.desc())).all()
    return [{"id": i.id, "tranid": i.tranid, "trandate": i.trandate, "status": i.status,
             "total": float(i.total) if i.total is not None else None} for i in rows]


def invoice_with_lines(db: Session, customer_id: int, invoice_id: int):
    """An invoice (scoped to the customer) plus its charge lines, or (None, [])."""
    inv = db.get(Invoice, invoice_id)
    if not inv or inv.customer_id != customer_id:
        return None, []
    lines = db.scalars(
        select(InvoiceLine).where(InvoiceLine.invoice_id == inv.id)
        .order_by(InvoiceLine.id)).all()
    rows = [{"charge_type": l.charge_type, "description": l.description,
             "qty": float(l.qty) if l.qty is not None else None,
             "rate": float(l.rate) if l.rate is not None else None,
             "amount": float(l.amount) if l.amount is not None else None} for l in lines]
    return inv, rows


def rate_card_lines(db: Session, customer_id: int) -> list[dict]:
    card = active_rate_card(db, customer_id, date.today())
    if not card:
        return []
    order = {"container_unload": 0, "putaway": 1, "storage": 2,
             "picking_so": 3, "picking_vrma": 4, "shipping": 5}
    return [{"label": l.label, "rate": float(l.rate), "basis": l.basis}
            for l in sorted(card.lines, key=lambda x: order.get(x.charge_type, 9))]


# --- nav + week anchoring ----------------------------------------------------
def nav_counts(db: Session, customer_id: int, imap: dict) -> dict:
    return {
        "stock_on_order": len({r["tranid"] for r in stock_on_order(db, customer_id, imap)}),
        "item_receipts": db.scalar(select(func.count()).select_from(ItemReceipt)
                                   .where(ItemReceipt.customer_id == customer_id)),
        "stock_on_hand": len(imap),
        "fulfilments": db.scalar(select(func.count()).select_from(ItemFulfilment)
                                 .where(ItemFulfilment.customer_id == customer_id)),
        "invoices": db.scalar(select(func.count()).select_from(Invoice)
                              .where(Invoice.customer_id == customer_id)),
    }


def latest_activity_date(db: Session, customer_id: int) -> date | None:
    candidates = [
        db.scalar(select(func.max(ItemReceipt.trandate)).where(ItemReceipt.customer_id == customer_id)),
        db.scalar(select(func.max(ItemFulfilment.trandate)).where(ItemFulfilment.customer_id == customer_id)),
        db.scalar(select(func.max(InboundShipment.received_date)).where(InboundShipment.customer_id == customer_id)),
        db.scalar(select(func.max(StockOnHand.snapshot_date)).where(StockOnHand.customer_id == customer_id)),
    ]
    dates = [d for d in candidates if d]
    return max(dates) if dates else None


def week_bounds(d: date) -> tuple[date, date]:
    """Monday–Sunday week containing d."""
    mon = d - timedelta(days=d.weekday())
    return mon, mon + timedelta(days=6)


# --- overview ----------------------------------------------------------------
def overview(db: Session, customer: Customer, imap: dict) -> dict:
    soh = stock_on_hand(db, customer.id, imap)
    soo = stock_on_order(db, customer.id, imap)
    anchor = latest_activity_date(db, customer.id) or date.today()
    wk_start, wk_end = week_bounds(anchor)

    # current week charge breakdown + 4-week history, both from the billing engine
    cur = compute_billing(db, customer, wk_start, wk_end)
    by_type = {l.charge_type: l for l in cur.lines}
    history = []
    for i in range(3, -1, -1):
        s, e = week_bounds(anchor - timedelta(days=7 * i))
        res = compute_billing(db, customer, s, e)
        history.append({"label": s.strftime("%d %b"), "total": res.total})

    recent = (item_receipts(db, customer.id, imap)[:2] +
              fulfilments(db, customer.id, imap)[:3])
    for r in recent:
        r["kind"] = "Receipt" if "shipment" in r else "Fulfilment"
    recent.sort(key=lambda r: r["trandate"] or date.min, reverse=True)

    return {
        "brand": customer.brand_label or "", "location": customer.location_label or "",
        "skus": len(imap),
        "soh_synced_at": soh_synced_at(db, customer.id),
        "units_on_hand": sum(r["qty_on_hand"] for r in soh),
        "pallets": sum(r["pallets"] for r in soh),
        "storage_per_week": sum(r["storage_per_week"] for r in soh),
        "units_on_order": sum(r["outstanding"] for r in soo),
        "open_pos": len({r["tranid"] for r in soo}),
        "week_start": wk_start, "week_end": wk_end,
        "week_total": cur.total,
        "week_lines": [{"type": ct, "label": (by_type[ct].label if ct in by_type else lbl),
                        "qty": (by_type[ct].qty if ct in by_type else 0),
                        "amount": (by_type[ct].amount if ct in by_type else 0.0)}
                       for ct, lbl in [("container_unload", "Container unload"),
                                       ("putaway", "Putaway"), ("storage", "Storage"),
                                       ("picking_so", "Picking — SO"),
                                       ("picking_vrma", "Picking — VRMA")]],
        "history": history,
        "history_max": max((h["total"] for h in history), default=0) or 1,
        "recent": recent,
    }
