"""Connectors (adapters) for the systems the hub integrates with.

Each connector hides one system's tables and quirks behind a small interface,
so the orchestration logic in service.py reads like the business process.
In production these would be REST/SOAP/IDoc calls; here they hit SQLite.
"""
import random
from datetime import datetime, timedelta

from . import config as C


class TransientError(Exception):
    """A failure that may succeed on retry (timeout, system briefly unavailable)."""


class DMSUnavailable(TransientError):
    pass


class ERPConnector:
    def __init__(self, conn):
        self.conn = conn

    def get_machine(self, machine_id):
        return self.conn.execute("SELECT * FROM erp_machines WHERE machine_id = ?", (machine_id,)).fetchone()

    def get_customer(self, customer_id):
        return self.conn.execute("SELECT * FROM erp_customers WHERE customer_id = ?", (customer_id,)).fetchone()

    def get_dealer(self, dealer_id):
        return self.conn.execute("SELECT * FROM erp_dealers WHERE dealer_id = ?", (dealer_id,)).fetchone()

    def get_part(self, part_number):
        return self.conn.execute("SELECT * FROM erp_parts WHERE part_number = ?", (part_number,)).fetchone()

    def fault_exists(self, fault_code):
        return self.conn.execute("SELECT 1 FROM erp_fault_catalog WHERE fault_code = ?",
                                 (fault_code,)).fetchone() is not None

    def parts_kit(self, fault_code):
        return self.conn.execute(
            """SELECT fp.part_number, fp.quantity, p.list_price FROM erp_fault_parts fp
               JOIN erp_parts p USING (part_number) WHERE fp.fault_code = ? ORDER BY fp.part_number""",
            (fault_code,)).fetchall()

    def dealers_in_region(self, region, exclude):
        rows = self.conn.execute("SELECT dealer_id FROM erp_dealers WHERE region = ? AND dealer_id != ? "
                                 "ORDER BY dealer_id", (region, exclude)).fetchall()
        return [r["dealer_id"] for r in rows]

    def open_order_for_fault(self, machine_id, fault_code, since_iso):
        return self.conn.execute(
            """SELECT order_id FROM erp_orders WHERE machine_id = ? AND fault_code = ?
               AND status IN ('RESERVED','PARTIAL_BACKORDER','BACKORDERED') AND created_at >= ?""",
            (machine_id, fault_code, since_iso)).fetchone()

    def next_order_id(self):
        n = self.conn.execute("SELECT COUNT(*) FROM erp_orders").fetchone()[0]
        return f"SO-{n + 1:07d}"

    def create_order(self, header, lines):
        self.conn.execute(
            """INSERT INTO erp_orders (order_id, channel, source_message_id, customer_id, machine_id, fault_code,
               dealer_id, created_at, status, fulfillment, total_value, ai_assisted)
               VALUES (:order_id, :channel, :source_message_id, :customer_id, :machine_id, :fault_code,
               :dealer_id, :created_at, :status, :fulfillment, :total_value, :ai_assisted)""", header)
        self.conn.executemany(
            """INSERT INTO erp_order_lines (order_id, line_no, part_number, quantity, unit_price,
               fulfilling_dealer_id, line_status) VALUES (?,?,?,?,?,?,?)""",
            [(header["order_id"], i + 1, ln["part_number"], ln["quantity"], ln["unit_price"],
              ln["fulfilling_dealer_id"], ln["line_status"]) for i, ln in enumerate(lines)])


class DMSConnector:
    """Dealer Management System. Simulates real reliability problems:
    random timeouts and dealer-specific outage windows."""

    def __init__(self, conn, seed=C.SEED, failure_rate=C.DMS_TRANSIENT_FAILURE_RATE,
                 outages=C.DMS_OUTAGE_WINDOWS):
        self.conn = conn
        self.rng = random.Random(seed)
        self.failure_rate = failure_rate
        start = datetime.fromisoformat(C.SIM_START)
        self.outages = [(d, start + timedelta(days=s), start + timedelta(days=e)) for d, s, e in outages]
        self.calls = 0

    def _check_up(self, dealer_id, as_of):
        self.calls += 1
        for d, s, e in self.outages:
            if d == dealer_id and s <= as_of < e:
                raise DMSUnavailable(f"{dealer_id} DMS outage until {e.date()}")
        if self.rng.random() < self.failure_rate:
            raise DMSUnavailable(f"{dealer_id} DMS timeout")

    def available(self, dealer_id, part_number, as_of):
        self._check_up(dealer_id, as_of)
        row = self.conn.execute("SELECT on_hand - reserved AS avail FROM dms_inventory "
                                "WHERE dealer_id = ? AND part_number = ?", (dealer_id, part_number)).fetchone()
        return row["avail"] if row else 0

    def end_of_day(self):
        """Nightly DMS batch: reserved parts ship to customers, then stock below the
        reorder point is replenished to target (supplier delivery)."""
        self.conn.execute("UPDATE dms_inventory SET on_hand = on_hand - reserved, reserved = 0")
        low = self.conn.execute("SELECT dealer_id, part_number FROM dms_inventory WHERE on_hand <= reorder_point "
                                "AND target_stock > 0 ORDER BY dealer_id, part_number").fetchall()
        arriving = [(r["dealer_id"], r["part_number"]) for r in low if self.rng.random() < 1 / 5]  # ~5-day lead time
        self.conn.executemany("UPDATE dms_inventory SET on_hand = target_stock WHERE dealer_id = ? "
                              "AND part_number = ?", arriving)
        self.conn.commit()

    def reserve(self, dealer_id, part_number, qty, as_of):
        self._check_up(dealer_id, as_of)
        cur = self.conn.execute(
            "UPDATE dms_inventory SET reserved = reserved + ? WHERE dealer_id = ? AND part_number = ? "
            "AND on_hand - reserved >= ?", (qty, dealer_id, part_number, qty))
        if cur.rowcount != 1:
            raise ValueError(f"Insufficient stock to reserve {qty} x {part_number} at {dealer_id}")
