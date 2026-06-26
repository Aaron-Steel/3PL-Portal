"""Billing engine — derive the weekly 3PL service charges from cached NetSuite data.

Replaces the manual weekly saved searches (brief, priority 2). Given a customer and a
period, computes one billing line per charge type off the active rate card:

  container_unload  count of inbound shipments received in period       x per_container
  putaway           sum of item-receipt-line units in period            x per_unit
  storage           sum of pallets per snapshot week in period          x per_pallet_week
  picking_so        sum of SO-fulfilment units in period                x per_unit
  picking_vrma      sum of VRMA-fulfilment units in period              x per_unit

Pallets = ceil(qty_on_hand / units_per_pallet) per item, summed per weekly snapshot
(see docs/netsuite_validation.md for the snapshot decision). Fulfilment units are
positive-only — the cache already stores positives, but we guard here too.

This module is pure: it reads the cache and returns a result. Persisting a BillingRun
and pushing a draft invoice to NetSuite are separate steps (service / netsuite layers).
"""
import json
import math
from dataclasses import dataclass, field
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import (Customer, InboundShipment, ItemFulfilment, ItemFulfilmentLine,
                     ItemReceipt, ItemReceiptLine, RateCard, RateCardLine, StockOnHand)


@dataclass
class ComputedLine:
    charge_type: str
    label: str
    qty: float
    rate: float
    amount: float
    basis: str
    source_refs: list = field(default_factory=list)


@dataclass
class BillingResult:
    customer_id: int
    period_start: date
    period_end: date
    lines: list  # list[ComputedLine]

    @property
    def total(self) -> float:
        return round(sum(l.amount for l in self.lines), 2)


def active_rate_card(db: Session, customer_id: int, on: date) -> RateCard | None:
    """The rate card effective on `on` for the customer (latest effective_from <= on)."""
    cards = db.scalars(
        select(RateCard).where(RateCard.customer_id == customer_id)
        .order_by(RateCard.effective_from.desc())).all()
    for c in cards:
        if c.effective_from <= on and (c.effective_to is None or c.effective_to >= on):
            return c
    return None


def _rates(card: RateCard) -> dict[str, RateCardLine]:
    return {l.charge_type: l for l in card.lines}


def compute_billing(db: Session, customer: Customer,
                    period_start: date, period_end: date) -> BillingResult:
    card = active_rate_card(db, customer.id, period_end)
    if card is None:
        return BillingResult(customer.id, period_start, period_end, [])
    rates = _rates(card)
    lines: list[ComputedLine] = []

    def add(charge_type: str, qty: float, refs: list):
        rc = rates.get(charge_type)
        if rc is None or qty == 0:
            return
        amount = round(qty * float(rc.rate), 2)
        lines.append(ComputedLine(charge_type, rc.label, qty, float(rc.rate),
                                  amount, rc.basis, refs))

    # --- container unload: inbound shipments received in period ---------------
    shipments = db.scalars(
        select(InboundShipment).where(
            InboundShipment.customer_id == customer.id,
            InboundShipment.received_date != None,                      # noqa: E711
            InboundShipment.received_date >= period_start,
            InboundShipment.received_date <= period_end)).all()
    add("container_unload", len(shipments),
        [s.shipment_number or s.ns_shipment_id for s in shipments])

    # --- putaway: item-receipt units in period --------------------------------
    receipts = db.scalars(
        select(ItemReceipt).where(
            ItemReceipt.customer_id == customer.id,
            ItemReceipt.trandate >= period_start,
            ItemReceipt.trandate <= period_end)).all()
    putaway_units = sum(float(l.qty) for r in receipts for l in r.lines)
    add("putaway", putaway_units, [r.tranid or r.ns_receipt_id for r in receipts])

    # --- storage: pallets per weekly snapshot in period -----------------------
    # Sum pallets across items for each snapshot date that falls in the period;
    # each weekly snapshot is one "pallet-week" of charge.
    snaps = db.scalars(
        select(StockOnHand).where(
            StockOnHand.customer_id == customer.id,
            StockOnHand.snapshot_date >= period_start,
            StockOnHand.snapshot_date <= period_end)).all()
    pallet_weeks = 0.0
    snap_refs = []
    for s in snaps:
        if s.pallets is not None:
            pallets = float(s.pallets)
        elif s.units_per_pallet:
            pallets = math.ceil(float(s.qty_on_hand) / s.units_per_pallet)
        else:
            pallets = 0.0
        pallet_weeks += pallets
    if snaps:
        snap_refs = sorted({s.snapshot_date.isoformat() for s in snaps})
    add("storage", pallet_weeks, snap_refs)

    # --- picking: SO and VRMA fulfilment units in period ----------------------
    for charge_type, source in (("picking_so", "SO"), ("picking_vrma", "VRMA")):
        fulfils = db.scalars(
            select(ItemFulfilment).where(
                ItemFulfilment.customer_id == customer.id,
                ItemFulfilment.source_type == source,
                ItemFulfilment.trandate >= period_start,
                ItemFulfilment.trandate <= period_end)).all()
        units = sum(max(0.0, float(l.qty)) for f in fulfils for l in f.lines)
        add(charge_type, units, [f.tranid or f.ns_fulfilment_id for f in fulfils])

    return BillingResult(customer.id, period_start, period_end, lines)


def result_to_run_kwargs(res: BillingResult) -> list[dict]:
    """Shape ComputedLines into BillingLine column dicts (source_refs -> JSON)."""
    return [{
        "charge_type": l.charge_type, "description": l.label, "qty": l.qty,
        "rate": l.rate, "amount": l.amount, "source_refs": json.dumps(l.source_refs),
    } for l in res.lines]
