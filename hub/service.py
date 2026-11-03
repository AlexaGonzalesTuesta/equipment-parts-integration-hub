"""Integration hub orchestration.

Two inbound channels feed one canonical order process:
  TELEMATICS  machine fault event -> diagnose -> parts kit -> source -> ERP order
  ECOMMERCE   storefront order    -> validate -> price check -> source -> ERP order

Reliability patterns used (see docs/02_solution_design.md):
  * Idempotency: a message id is only ever turned into one order.
  * Validation at the edge: bad payloads never reach ERP.
  * Retry with exponential backoff for transient DMS failures.
  * All-or-nothing transactions: if any step fails, reservations roll back.
  * Dead letter queue with a reason code, and replay once the cause is fixed.
  * Audit trail for every step of every message.
"""
import hashlib
import json
import time
from datetime import datetime, timedelta, timezone

from pydantic import ValidationError

from . import config as C
from .connectors import DMSConnector, ERPConnector, TransientError
from .models import ProcessingResult, TelematicsEvent, WebOrder

TELEMATICS, ECOMMERCE = "TELEMATICS", "ECOMMERCE"


def now_iso(spec="seconds"):
    return datetime.now(timezone.utc).replace(tzinfo=None).isoformat(timespec=spec)


class DeadLetter(Exception):
    def __init__(self, reason_code, detail, replayable):
        super().__init__(detail)
        self.reason_code, self.detail, self.replayable = reason_code, detail, replayable


class Rejected(Exception):
    def __init__(self, reason_code, detail):
        super().__init__(detail)
        self.reason_code, self.detail = reason_code, detail


class IntegrationHub:
    def __init__(self, conn, recommender, dms=None, sleep=False):
        self.conn = conn
        self.erp = ERPConnector(conn)
        self.dms = dms or DMSConnector(conn)
        self.ai = recommender
        self.sleep = sleep          # False in simulation/tests: backoff is recorded, not waited
        self._audit = []

    # ------------------------------------------------------------------ public API
    def process_telematics(self, payload: dict, as_of=None, replay=False) -> ProcessingResult:
        return self._process(TELEMATICS, payload, payload.get("event_id"), self._telematics_flow, as_of, replay)

    def process_web_order(self, payload: dict, as_of=None, replay=False) -> ProcessingResult:
        return self._process(ECOMMERCE, payload, payload.get("web_order_id"), self._web_flow, as_of, replay)

    def replay_dead_letters(self, as_of=None):
        """Reprocess replayable dead letters (for example after a DMS outage ends)."""
        as_of = as_of or datetime.now()
        rows = self.conn.execute("SELECT * FROM hub_dead_letter WHERE replayable = 1 AND resolved_at IS NULL "
                                 "ORDER BY id").fetchall()
        results = []
        for r in rows:
            payload = json.loads(r["payload"])
            fn = self.process_telematics if r["channel"] == TELEMATICS else self.process_web_order
            res = fn(payload, as_of=as_of, replay=True)
            if res.outcome != "DEAD_LETTER":
                self.conn.execute("UPDATE hub_dead_letter SET resolved_at = ? WHERE id = ?",
                                  (now_iso(), r["id"]))
                self.conn.commit()
            results.append(res)
        return results

    def replay_one(self, message_id, as_of=None):
        """Reprocess a single open, replayable dead letter (IT support fixes one message at a time)."""
        row = self.conn.execute("SELECT * FROM hub_dead_letter WHERE message_id = ? AND replayable = 1 "
                                "AND resolved_at IS NULL", (message_id,)).fetchone()
        if row is None:
            return None
        payload = json.loads(row["payload"])
        fn = self.process_telematics if row["channel"] == TELEMATICS else self.process_web_order
        res = fn(payload, as_of=as_of or datetime.now(), replay=True)
        if res.outcome != "DEAD_LETTER":
            self.conn.execute("UPDATE hub_dead_letter SET resolved_at = ? WHERE id = ?", (now_iso(), row["id"]))
            self.conn.commit()
        return res

    # ------------------------------------------------------------------ core wrapper
    def _process(self, channel, payload, message_id, flow, as_of, replay):
        t0 = time.perf_counter()
        if not message_id:
            digest = hashlib.md5(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()[:10]
            message_id = f"NOID-{digest}"
        message_id = str(message_id)
        self._audit = []
        now = now_iso()

        prior = self.conn.execute("SELECT status FROM hub_inbound WHERE message_id = ?", (message_id,)).fetchone()
        if prior and not (replay and prior["status"] == "DEAD_LETTER"):
            self._log(message_id, "IDEMPOTENCY_CHECK", "DUPLICATE", f"already {prior['status']}")
            return self._finish(message_id, channel, "DUPLICATE", t0, detail=f"Already received ({prior['status']})")
        if not prior:
            self.conn.execute("INSERT INTO hub_inbound VALUES (?,?,?,?,?)",
                              (message_id, channel, now, json.dumps(payload, default=str), "RECEIVED"))
            self.conn.commit()

        attempt = 1 + self.conn.execute("SELECT COUNT(*) FROM hub_results WHERE message_id = ?",
                                        (message_id,)).fetchone()[0]
        try:
            outcome, order_id, status, detail = flow(message_id, payload, as_of)
            self.conn.commit()
        except ValidationError as e:
            self.conn.rollback()
            first = e.errors()[0]
            detail = f"{'.'.join(str(p) for p in first['loc'])}: {first['msg']}"
            return self._dead_letter(message_id, channel, payload, "VALIDATION_FAILED", detail, False, t0,
                                     attempt, outcome="REJECTED")
        except Rejected as e:
            self.conn.rollback()
            return self._dead_letter(message_id, channel, payload, e.reason_code, e.detail, False, t0,
                                     attempt, outcome="REJECTED")
        except DeadLetter as e:
            self.conn.rollback()
            return self._dead_letter(message_id, channel, payload, e.reason_code, e.detail, e.replayable, t0, attempt)

        self.conn.execute("UPDATE hub_inbound SET status = ? WHERE message_id = ?", (outcome, message_id))
        return self._finish(message_id, channel, outcome, t0, order_id, status, detail, attempt)

    # ------------------------------------------------------------------ channel flows
    def _telematics_flow(self, mid, payload, as_of):
        ev = TelematicsEvent(**payload)
        self._log(mid, "VALIDATE", "OK")
        as_of = as_of or ev.occurred_at

        if ev.severity < C.MIN_SEVERITY_FOR_ORDER:
            self._log(mid, "SEVERITY_RULE", "SKIP", f"severity {ev.severity} is informational")
            return "NO_ACTION", None, None, "Informational fault, logged for monitoring"

        machine = self.erp.get_machine(ev.machine_id)
        if machine is None:
            raise DeadLetter("MASTER_DATA_MISSING", f"Machine {ev.machine_id} not found in ERP", True)
        self._log(mid, "ENRICH", "OK", f"customer {machine['customer_id']}, dealer {machine['dealer_id']}")

        ai_used = False
        if ev.fault_code and self.erp.fault_exists(ev.fault_code):
            fault = ev.fault_code
            self._log(mid, "DIAGNOSE", "RULE", f"fault code {fault} found in catalog")
        else:
            pred = self.ai.predict(ev.symptom_note)
            ai_used = True
            self._log(mid, "DIAGNOSE", "AI", f"predicted {pred.fault_code} ({pred.confidence:.2f})")
            if pred.confidence < C.AI_CONFIDENCE_THRESHOLD or not ev.symptom_note.strip():
                self.conn.execute(
                    "INSERT OR IGNORE INTO hub_manual_review (message_id, machine_id, predicted_fault, confidence, "
                    "top3, symptom_note, created_at) VALUES (?,?,?,?,?,?,?)",
                    (mid, ev.machine_id, pred.fault_code, pred.confidence, json.dumps(pred.top3),
                     ev.symptom_note, now_iso()))
                return "MANUAL_REVIEW", None, None, (f"AI confidence {pred.confidence:.2f} below "
                                                     f"{C.AI_CONFIDENCE_THRESHOLD}; sent to service advisor")
            fault = pred.fault_code

        since = (ev.occurred_at - timedelta(days=7)).isoformat()
        existing = self.erp.open_order_for_fault(ev.machine_id, fault, since)
        if existing:
            self._log(mid, "DEDUPE_RULE", "SKIP", f"open order {existing['order_id']}")
            return "NO_ACTION", existing["order_id"], None, "Open order already covers this fault (7-day rule)"

        kit = self.erp.parts_kit(fault)
        lines = [{"part_number": k["part_number"], "quantity": k["quantity"], "unit_price": k["list_price"]}
                 for k in kit]
        dealer = self.erp.get_dealer(machine["dealer_id"])
        sourced = self._source(mid, dealer, lines, as_of)
        return self._create_order(mid, TELEMATICS, machine["customer_id"], ev.machine_id, fault, dealer,
                                  sourced, ev.occurred_at, ai_used)

    def _web_flow(self, mid, payload, as_of):
        wo = WebOrder(**payload)
        self._log(mid, "VALIDATE", "OK")
        as_of = as_of or wo.submitted_at

        if self.erp.get_customer(wo.customer_id) is None:
            raise DeadLetter("MASTER_DATA_MISSING", f"Customer {wo.customer_id} not found in ERP", True)
        dealer = self.erp.get_dealer(wo.dealer_id)
        if dealer is None:
            raise DeadLetter("MASTER_DATA_MISSING", f"Dealer {wo.dealer_id} not found in ERP", True)

        lines = []
        for ln in wo.lines:
            part = self.erp.get_part(ln.part_number)
            if part is None:
                raise Rejected("UNKNOWN_PART", f"Part {ln.part_number} is not in the ERP catalog")
            if abs(ln.unit_price - part["list_price"]) > C.PRICE_TOLERANCE * part["list_price"]:
                raise DeadLetter("PRICE_MISMATCH", f"{ln.part_number}: storefront {ln.unit_price:.2f} vs ERP "
                                 f"{part['list_price']:.2f}", False)
            lines.append({"part_number": ln.part_number, "quantity": ln.quantity, "unit_price": part["list_price"]})
        self._log(mid, "PRICE_CHECK", "OK")

        sourced = self._source(mid, dealer, lines, as_of)
        return self._create_order(mid, ECOMMERCE, wo.customer_id, None, None, dealer, sourced,
                                  wo.submitted_at, False)

    # ------------------------------------------------------------------ shared steps
    def _with_retry(self, mid, label, fn, *args):
        delay = C.BACKOFF_BASE_MS
        for attempt in range(1, C.MAX_RETRIES + 1):
            try:
                return fn(*args)
            except TransientError as e:
                self._log(mid, label, "RETRY" if attempt < C.MAX_RETRIES else "FAILED", f"try {attempt}: {e}")
                if attempt == C.MAX_RETRIES:
                    raise DeadLetter("DMS_UNAVAILABLE", str(e), True)
                if self.sleep:
                    time.sleep(delay / 1000)
                delay *= 2

    def _source(self, mid, dealer, lines, as_of):
        """Find stock for each line: assigned dealer first, then other dealers in the region, else backorder."""
        out = []
        for ln in lines:
            pn, qty = ln["part_number"], ln["quantity"]
            chosen, kind = None, "BACKORDERED"
            if self._with_retry(mid, "DMS_AVAILABILITY", self.dms.available, dealer["dealer_id"], pn, as_of) >= qty:
                chosen, kind = dealer["dealer_id"], "RESERVED"
            else:
                for alt in self.erp.dealers_in_region(dealer["region"], dealer["dealer_id"]):
                    if self._with_retry(mid, "DMS_AVAILABILITY", self.dms.available, alt, pn, as_of) >= qty:
                        chosen, kind = alt, "TRANSFER"
                        break
            if chosen:
                self._with_retry(mid, "DMS_RESERVE", self.dms.reserve, chosen, pn, qty, as_of)
            self._log(mid, "SOURCE", kind, f"{qty} x {pn} at {chosen or 'none'}")
            out.append({**ln, "fulfilling_dealer_id": chosen, "line_status": kind})
        return out

    def _create_order(self, mid, channel, customer_id, machine_id, fault, dealer, lines, created_at, ai_used):
        kinds = {ln["line_status"] for ln in lines}
        if kinds == {"BACKORDERED"}:
            status = "BACKORDERED"
        elif "BACKORDERED" in kinds:
            status = "PARTIAL_BACKORDER"
        else:
            status = "RESERVED"
        fulfillment = ("BACKORDER" if "BACKORDERED" in kinds else
                       "REGIONAL_TRANSFER" if "TRANSFER" in kinds else "ASSIGNED_DEALER")
        order_id = self.erp.next_order_id()
        header = {"order_id": order_id, "channel": channel, "source_message_id": mid, "customer_id": customer_id,
                  "machine_id": machine_id, "fault_code": fault, "dealer_id": dealer["dealer_id"],
                  "created_at": created_at.isoformat(), "status": status, "fulfillment": fulfillment,
                  "total_value": round(sum(ln["quantity"] * ln["unit_price"] for ln in lines), 2),
                  "ai_assisted": int(ai_used)}
        self.erp.create_order(header, lines)
        self._log(mid, "ERP_ORDER", "CREATED", f"{order_id} {status}")
        return "ORDER_CREATED", order_id, status, f"{fulfillment.replace('_', ' ').title()}"

    # ------------------------------------------------------------------ logging helpers
    def _log(self, mid, step, status, detail=""):
        self._audit.append((mid, step, status, detail, now_iso("milliseconds")))

    def _flush_audit(self):
        self.conn.executemany("INSERT INTO hub_audit (message_id, step, status, detail, logged_at) "
                              "VALUES (?,?,?,?,?)", self._audit)
        self._audit = []

    def _dead_letter(self, mid, channel, payload, reason, detail, replayable, t0, attempt, outcome="DEAD_LETTER"):
        self._log(mid, "DEAD_LETTER", reason, detail)
        open_dl = self.conn.execute("SELECT id FROM hub_dead_letter WHERE message_id = ? AND resolved_at IS NULL",
                                    (mid,)).fetchone()
        if open_dl:
            self.conn.execute("UPDATE hub_dead_letter SET attempts = attempts + 1, detail = ? WHERE id = ?",
                              (detail, open_dl["id"]))
        else:
            self.conn.execute(
                "INSERT INTO hub_dead_letter (message_id, channel, reason_code, detail, payload, attempts, replayable, "
                "created_at) VALUES (?,?,?,?,?,?,?,?)",
                (mid, channel, reason, detail, json.dumps(payload, default=str), 1, int(replayable),
                 now_iso()))
        self.conn.execute("UPDATE hub_inbound SET status = ? WHERE message_id = ?", (outcome, mid))
        return self._finish(mid, channel, outcome, t0, detail=f"{reason}: {detail}", attempt=attempt)

    def _finish(self, mid, channel, outcome, t0, order_id=None, status=None, detail="", attempt=1):
        latency = (time.perf_counter() - t0) * 1000
        self._flush_audit()
        self.conn.execute("INSERT INTO hub_results VALUES (?,?,?,?,?,?,?,?)",
                          (mid, channel, outcome, order_id, detail, round(latency, 3), attempt,
                           now_iso()))
        self.conn.commit()
        return ProcessingResult(message_id=mid, channel=channel, outcome=outcome, order_id=order_id,
                                order_status=status, detail=detail, latency_ms=round(latency, 3))
