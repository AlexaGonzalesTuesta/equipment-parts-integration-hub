"""Demo scenarios and result rendering for the parts hub console.

No Streamlit here: the live console (app/console.py) and the static site builder
(site/build_site.py) both import this module, so they run the same scenarios through the
same hub code and render the same results.
"""
import html
import json
import re
from datetime import datetime, timedelta

from hub import config as C
from hub.connectors import DMSConnector, ERPConnector
from hub.service import IntegrationHub

# ----------------------------------------------------------------------------- reference text
OUTCOME_CHIP = {"ORDER_CREATED": ("Order created", "ok"), "DUPLICATE": ("Duplicate blocked", "grey"),
                "NO_ACTION": ("No order needed", "grey"), "MANUAL_REVIEW": ("Service advisor review", "warn"),
                "DEAD_LETTER": ("Held for a person", "bad"), "REJECTED": ("Rejected", "bad")}
LINE_CHIP = {"RESERVED": ("Reserved", "ok"), "TRANSFER": ("Regional transfer", "info"),
             "BACKORDERED": ("Backordered", "warn")}
REASONS = {
    "DMS_UNAVAILABLE": ("Dealer inventory system did not respond", "IT support",
                        "Replay once the dealer system is back"),
    "MASTER_DATA_MISSING": ("Machine, customer or dealer not set up in ERP", "Master data team",
                            "Add the record in ERP, then replay"),
    "VALIDATION_FAILED": ("Message broke the agreed data contract", "Sending system team",
                          "Fix the sender; the hub never guesses at bad data"),
    "PRICE_MISMATCH": ("Storefront price differs from the ERP price list", "Pricing team",
                       "Confirm the price with the customer"),
    "UNKNOWN_PART": ("Part number not in the ERP catalog", "E-commerce team",
                     "Offer the replacement part"),
}
STEP_LABEL = {"IDEMPOTENCY_CHECK": "Duplicate check", "VALIDATE": "Message checked against the data contract",
              "SEVERITY_RULE": "Severity rule", "ENRICH": "Machine, customer and dealer looked up in ERP",
              "DEDUPE_RULE": "Open order check (7 days)", "DMS_AVAILABILITY": "Dealer stock check",
              "DMS_RESERVE": "Stock reservation", "SOURCE": "Part sourced", "ERP_ORDER": "ERP sales order",
              "PRICE_CHECK": "Price checked against ERP price list", "DEAD_LETTER": "Held for a person"}
STATUS_TEXT = {"OK": "OK", "RULE": "Catalog", "AI": "AI", "CREATED": "Created", "SKIP": "Skipped",
               "DUPLICATE": "Duplicate", "RETRY": "Retry", "FAILED": "Failed", "TRANSFER": "Transfer",
               "RESERVED": "Reserved", "BACKORDERED": "Backordered", "DMS_UNAVAILABLE": "Dealer system down",
               "MASTER_DATA_MISSING": "Not in ERP", "VALIDATION_FAILED": "Bad data", "PRICE_MISMATCH": "Price mismatch",
               "UNKNOWN_PART": "Unknown part"}
STATUS_CHIP = {"OK": "ok", "RULE": "ok", "AI": "info", "RESERVED": "ok", "TRANSFER": "info", "BACKORDERED": "warn",
               "CREATED": "ok", "SKIP": "grey", "DUPLICATE": "grey", "RETRY": "warn", "FAILED": "bad"}

SCENARIOS = [
    ("assigned", "Fault code, parts in stock",
     "An excavator reports a known fault. Normally: a phone call, a technician visit, then someone re-keys the order."),
    ("transfer", "Assigned dealer is out of stock",
     "The hub checks the other dealers in the region instead of leaving the machine waiting."),
    ("ai_ok", "No fault code, operator note only",
     "Older machines send no code. The AI reads the note and orders the kit when it is confident."),
    ("ai_unsure", "Vague note, AI not confident",
     "Below 65% confidence nothing is ordered. A service advisor decides, so wrong parts never ship."),
    ("duplicate", "Same event sent twice",
     "Telematics platforms resend on network errors. The customer must never get two orders."),
    ("outage", "Dealer system is down",
     "The dealer's inventory system stops answering. The order is held whole, then replayed."),
    ("price", "Storefront price is out of date",
     "A web order arrives with a stale price. It is held for the pricing team instead of billing the wrong amount."),
]



def now():
    return datetime.now().replace(microsecond=0)


def next_id(state, prefix):
    state["sent"] += 1
    return f"{prefix}-{state['sandbox_id']}-{state['sent']:03d}"


def reliable_hub(conn, model, outages=()):
    return IntegrationHub(conn, model, dms=DMSConnector(conn, failure_rate=0.0, outages=list(outages)))


def kit(conn, fault):
    return [(r["part_number"], r["quantity"]) for r in
            conn.execute("SELECT part_number, quantity FROM erp_fault_parts WHERE fault_code = ? ORDER BY part_number",
                         (fault,))]


def avail(conn, dealer, pn):
    row = conn.execute("SELECT on_hand - reserved FROM dms_inventory WHERE dealer_id = ? AND part_number = ?",
                       (dealer, pn)).fetchone()
    return row[0] if row else 0


def machines(conn, state, machine_type=None):
    sql = ("SELECT m.machine_id, m.model, m.machine_type, m.dealer_id, d.region FROM erp_machines m "
           "JOIN erp_dealers d USING (dealer_id)")
    rows = conn.execute(sql + (" WHERE m.machine_type = ?" if machine_type else "") + " ORDER BY m.machine_id",
                        (machine_type,) if machine_type else ()).fetchall()
    used = set(state["used_machines"])
    return [r for r in rows if r["machine_id"] not in used]


def has_open_order(conn, machine_id, fault):
    since = (now() - timedelta(days=7)).isoformat()
    return ERPConnector(conn).open_order_for_fault(machine_id, fault, since) is not None


def region_alt(conn, m, pn, qty):
    for d in ERPConnector(conn).dealers_in_region(m["region"], m["dealer_id"]):
        if avail(conn, d, pn) >= qty:
            return d
    return None


def find_in_stock(conn, state, faults, machine_type=None):
    for m in machines(conn, state, machine_type):
        for f in faults:
            if not has_open_order(conn, m["machine_id"], f) and all(
                    avail(conn, m["dealer_id"], pn) >= q for pn, q in kit(conn, f)):
                return m, f
    return None, None


def find_transfer(conn, state, faults):
    """A machine whose own dealer lacks a part that another dealer in the region has."""
    fallback = None
    for m in machines(conn, state):
        for f in faults:
            if has_open_order(conn, m["machine_id"], f):
                continue
            parts = kit(conn, f)
            short = [(pn, q) for pn, q in parts if avail(conn, m["dealer_id"], pn) < q]
            covered = all(avail(conn, m["dealer_id"], pn) >= q or region_alt(conn, m, pn, q) for pn, q in parts)
            if short and covered:
                return m, f, None
            if fallback is None and all(region_alt(conn, m, pn, q) for pn, q in parts):
                fallback = (m, f)
    if fallback:  # no natural stock-out found: create one in this visitor's sandbox only
        m, f = fallback
        pn, q = kit(conn, f)[0]
        conn.execute("UPDATE dms_inventory SET on_hand = reserved WHERE dealer_id = ? AND part_number = ?",
                     (m["dealer_id"], pn))
        conn.commit()
        return m, f, pn
    return None, None, None


def telematics_payload(event_id, machine_id, fault, note, severity=3):
    return {"event_id": event_id, "machine_id": machine_id, "occurred_at": now().isoformat(), "fault_code": fault,
            "severity": severity, "symptom_note": note, "engine_hours": 6120.0}


def run_scenario(key, conn, model, state):
    """Send one demo scenario through the real hub. state holds sent, sandbox_id and used_machines.
    Returns the record the result panel renders from."""
    note_for = {f: n for f, n in [("COO-401", "engine overheating, coolant temp in red"),
                                  ("HYD-130", "hose burst on stick, spraying hydraulic oil"),
                                  ("ENG-230", "air filter restriction indicator red"),
                                  ("UND-620", "roller leaking oil")]}
    extra = {}
    if key in ("assigned", "duplicate", "outage"):
        m, f = find_in_stock(conn, state, ["HYD-130", "COO-401", "ENG-230", "UND-620"],
                             "Excavator" if key == "assigned" else None)
        if m is None:
            raise RuntimeError("No machine with parts in stock is left in this sandbox. Reset the sandbox.")
        payload = telematics_payload(next_id(state, "EV"), m["machine_id"], f, note_for.get(f, ""))
        if key == "assigned":
            reliable_hub(conn, model).process_telematics(payload)
        elif key == "duplicate":
            first = reliable_hub(conn, model).process_telematics(payload)
            reliable_hub(conn, model).process_telematics(dict(payload))
            extra["first_order"] = first.order_id
        else:
            offset = (now() - datetime.fromisoformat(C.SIM_START)).days
            reliable_hub(conn, model, [(m["dealer_id"], offset - 1, offset + 2)]).process_telematics(payload)
            extra["outage_dealer"] = m["dealer_id"]
        mid, channel = payload["event_id"], "TELEMATICS"
    elif key == "transfer":
        m, f, forced = find_transfer(conn, state, ["COO-401", "HYD-130", "UND-620", "ENG-230", "ELE-501", "FUE-310"])
        if m is None:
            raise RuntimeError("No regional stock is left in this sandbox. Reset the sandbox.")
        payload = telematics_payload(next_id(state, "EV"), m["machine_id"], f, note_for.get(f, ""))
        reliable_hub(conn, model).process_telematics(payload)
        mid, channel = payload["event_id"], "TELEMATICS"
        extra["forced_part"] = forced
    elif key in ("ai_ok", "ai_unsure"):
        notes = (["hose burst on stick, spraying hydraulic oil", "engine overheating, coolant temp in red",
                  "hydraulic hose split near fitting, oil everywhere"] if key == "ai_ok" else
                 ["machine making a strange noise, please advise", "noise from somewhere at the back",
                  "weird smell in cab"])
        note = next((n for n in notes if (model.predict(n).confidence >= C.AI_CONFIDENCE_THRESHOLD) == (key == "ai_ok")),
                    notes[0])
        fault = model.predict(note).fault_code
        m = next((m for m in machines(conn, state) if not has_open_order(conn, m["machine_id"], fault)), None)
        if m is None:
            raise RuntimeError("No machines left in this sandbox. Reset the sandbox.")
        payload = telematics_payload(next_id(state, "EV"), m["machine_id"], None, note)
        reliable_hub(conn, model).process_telematics(payload)
        mid, channel = payload["event_id"], "TELEMATICS"
    elif key == "price":
        cust = conn.execute("SELECT c.customer_id, d.dealer_id FROM erp_customers c JOIN erp_dealers d "
                            "ON d.region = c.region ORDER BY c.customer_id LIMIT 1 OFFSET ?",
                            (state["sent"] % 50,)).fetchone()
        part = conn.execute("SELECT part_number, list_price FROM erp_parts WHERE description LIKE 'Primary air filter'"
                            ).fetchone() or conn.execute("SELECT part_number, list_price FROM erp_parts LIMIT 1").fetchone()
        payload = {"web_order_id": next_id(state, "WEB"), "customer_id": cust["customer_id"], "dealer_id": cust["dealer_id"],
                   "submitted_at": now().isoformat(),
                   "lines": [{"part_number": part["part_number"], "quantity": 2,
                              "unit_price": round(part["list_price"] * 0.88, 2)}]}
        reliable_hub(conn, model).process_web_order(payload)
        mid, channel = payload["web_order_id"], "ECOMMERCE"
        m = None
    else:
        raise ValueError(key)
    if m is not None:
        state["used_machines"].append(m["machine_id"])
    return {"scenario": key, "message_id": mid, "channel": channel, **extra}


# ----------------------------------------------------------------------------- rendering
def esc(x):
    return html.escape("" if x is None else str(x))


def chip(text, kind):
    return f'<span class="chip {kind}">{esc(text)}</span>'


def money(x):
    return f"${x:,.2f}"


def dealer_names(conn):
    return {r["dealer_id"]: r["name"] for r in conn.execute("SELECT dealer_id, name FROM erp_dealers")}


def render_steps(conn, mid):
    rows = conn.execute("SELECT step, status, detail FROM hub_audit WHERE message_id = ? ORDER BY id",
                        (mid,)).fetchall()
    if not rows:
        return '<div class="small">No processing steps recorded.</div>'
    names = dealer_names(conn)
    items = []
    for r in rows:
        label = STEP_LABEL.get(r["step"], r["step"].replace("_", " ").title())
        if r["step"] == "DIAGNOSE":
            label = "AI read the operator note" if r["status"] == "AI" else "Fault code matched to the catalog"
        detail = r["detail"] or ""
        for did, name in names.items():
            detail = re.sub(rf"\b{did}\b", name, detail)
        kind = "bad" if r["step"] == "DEAD_LETTER" else STATUS_CHIP.get(r["status"], "grey")
        items.append(f'<li>{esc(label)} {chip(STATUS_TEXT.get(r["status"], r["status"].replace("_", " ").title()), kind)}'
                     f'{"<br><span class=d>" + esc(detail) + "</span>" if detail else ""}</li>')
    return '<ol class="steps">' + "".join(items) + "</ol>"


def render_order(conn, order_id):
    o = conn.execute("""SELECT o.*, m.model, m.machine_type, c.name AS customer, d.name AS dealer, d.region,
                               f.description AS fault_desc
                        FROM erp_orders o JOIN erp_dealers d USING (dealer_id)
                        JOIN erp_customers c USING (customer_id)
                        LEFT JOIN erp_machines m USING (machine_id)
                        LEFT JOIN erp_fault_catalog f USING (fault_code)
                        WHERE o.order_id = ?""", (order_id,)).fetchone()
    lines = conn.execute("""SELECT l.*, p.description FROM erp_order_lines l JOIN erp_parts p USING (part_number)
                            WHERE order_id = ? ORDER BY line_no""", (order_id,)).fetchall()
    names = dealer_names(conn)
    if o["machine_id"]:
        diag = conn.execute("SELECT detail FROM hub_audit WHERE message_id = ? AND step = 'DIAGNOSE' ORDER BY id DESC",
                            (o["source_message_id"],)).fetchone()
        conf = re.search(r"\(([\d.]+)\)", diag["detail"]) if diag and o["ai_assisted"] else None
        how = f"AI, {float(conf.group(1)):.0%} confident" if conf else "fault code from the machine"
        fault = f'<span class="mono">{esc(o["fault_code"])}</span> {esc(o["fault_desc"])}<br><span class="small">{esc(how)}</span>'
        machine = f'<span class="mono">{esc(o["machine_id"])}</span> {esc(o["model"])} {esc(o["machine_type"])}'
    else:
        fault, machine = "Storefront order", "n/a"
    grid = "".join(f'<div><div class="k">{k}</div><div class="v">{v}</div></div>' for k, v in [
        ("Sales order", f'<span class="mono">{esc(o["order_id"])}</span>'), ("Machine", machine), ("Fault", fault),
        ("Customer", esc(o["customer"])), ("Dealer", f'{esc(o["dealer"])}<br><span class="small">{esc(o["region"])}</span>'),
        ("Order value", money(o["total_value"]))])
    body = "".join(
        f'<tr><td class="mono">{esc(l["part_number"])}</td><td>{esc(l["description"])}</td>'
        f'<td class="num">{l["quantity"]}</td><td class="num">{money(l["unit_price"])}</td>'
        f'<td>{esc(names.get(l["fulfilling_dealer_id"], "No dealer in region has stock"))}</td>'
        f'<td>{chip(*LINE_CHIP.get(l["line_status"], (l["line_status"], "grey")))}</td></tr>' for l in lines)
    return (f'<div class="doc"><div class="doc-grid">{grid}</div><table class="lines"><thead><tr><th>Part no.</th>'
            f'<th>Description</th><th style="text-align:right">Qty</th><th style="text-align:right">Unit price</th>'
            f'<th>Ships from</th><th>Status</th></tr></thead><tbody>{body}</tbody></table></div>')


def result_parts(conn, last):
    """Everything the result panel shows for one message, as HTML strings.
    Shared by the Streamlit console and the static site builder."""
    mid = last["message_id"]
    res = conn.execute("SELECT * FROM hub_results WHERE message_id = ? ORDER BY rowid DESC LIMIT 1", (mid,)).fetchone()
    if res is None:
        return None
    outcome, detail = res["outcome"], res["detail"] or ""
    ms = res["latency_ms"]
    secs = "Processed in under 1 millisecond." if ms < 1 else f"Processed in {ms / 1000:.3f} seconds."
    out = {"outcome": outcome, "body": "", "caption": None, "detail": None, "replayable": False}

    if outcome == "ORDER_CREATED":
        o = conn.execute("SELECT fulfillment, status FROM erp_orders WHERE order_id = ?", (res["order_id"],)).fetchone()
        text = {"ASSIGNED_DEALER": "Every part is reserved at the machine's own dealer. No phone call, no re-keying.",
                "REGIONAL_TRANSFER": "The assigned dealer was short, so the hub reserved those parts at another "
                                     "dealer in the same region.",
                "BACKORDER": "Some parts are on backorder. The dealer sees it now instead of after a phone call."
                }[o["fulfillment"]]
        if last.get("scenario") == "outage":
            text = "Replayed after the dealer system came back. " + text
        out.update(kind="ok", kicker=f"Order {res['order_id']} created", text=f"{text} {secs}",
                   body=render_order(conn, res["order_id"]))
        if last.get("forced_part"):
            out["caption"] = (f"In this sandbox, the assigned dealer's stock of {last['forced_part']} was set to zero "
                              "to create this situation.")
    elif outcome == "DUPLICATE":
        out.update(kind="info", kicker="Resend blocked",
                   text=f"Order {esc(last.get('first_order'))} already covers this event, so the customer is not "
                        f"billed twice. The database also refuses a second order for the same message. {secs}",
                   body=render_order(conn, last["first_order"]) if last.get("first_order") else "")
    elif outcome == "MANUAL_REVIEW":
        r = conn.execute("SELECT * FROM hub_manual_review WHERE message_id = ?", (mid,)).fetchone()
        top3 = json.loads(r["top3"]) if r else []
        descs = {x["fault_code"]: x["description"]
                 for x in conn.execute("SELECT fault_code, description FROM erp_fault_catalog")}
        guesses = "".join(f'<tr><td class="mono">{esc(c)}</td><td>{esc(descs.get(c, ""))}</td>'
                          f'<td class="num">{p:.0%}</td></tr>' for c, p in top3)
        out.update(kind="warn", kicker="Sent to a service advisor",
                   text=f"The AI was only {r['confidence']:.0%} sure, below the {C.AI_CONFIDENCE_THRESHOLD:.0%} bar, "
                        f"so no parts were ordered. A wrong guess would ship the wrong parts to a job site. {secs}",
                   body=f'<div class="doc"><div class="small">Operator note</div><div style="margin:2px 0 10px 0">'
                        f'"{esc(r["symptom_note"])}"</div><table class="lines"><thead><tr><th>AI guess</th>'
                        f'<th>Fault</th><th style="text-align:right">Confidence</th></tr></thead>'
                        f'<tbody>{guesses}</tbody></table></div>')
    elif outcome in ("DEAD_LETTER", "REJECTED"):
        code = detail.split(":")[0]
        what, owner, step = REASONS.get(code, (detail, "IT support", "Investigate"))
        out.update(kind="bad", kicker=f"Held for {owner}",
                   text=f"{esc(what)}. Nothing was half-ordered. Next step: {esc(step)}. {secs}",
                   detail=f'<div class="small">System detail: <span class="mono">{esc(detail)}</span></div>')
        open_dl = conn.execute("SELECT 1 FROM hub_dead_letter WHERE message_id = ? AND replayable = 1 "
                               "AND resolved_at IS NULL", (mid,)).fetchone()
        out["replayable"] = bool(code == "DMS_UNAVAILABLE" and open_dl)
    else:
        out.update(kind="info", kicker=OUTCOME_CHIP.get(outcome, (outcome, ""))[0], text=f"{esc(detail)} {secs}")
    out["steps"] = (f'<div class="small" style="margin-bottom:6px">Message <span class="mono">{esc(mid)}</span>, '
                    f'{esc(last["channel"].title())}</div>' + render_steps(conn, mid))
    return out
