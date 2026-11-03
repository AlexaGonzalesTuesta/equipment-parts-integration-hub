"""Parts Order Integration Hub: dealer parts operations console.

Run:  streamlit run app/console.py

Every visitor works in a private copy of the database (a sandbox), so scenarios and
the reset button only change that visitor's data.
"""
import gzip
import html
import inspect
import json
import os
import re
import shutil
import sqlite3
import sys
import tempfile
import time
import uuid
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
import plotly.express as px
import streamlit as st

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from hub import config as C  # noqa: E402
from hub import metrics as M  # noqa: E402
from hub.connectors import DMSConnector, ERPConnector  # noqa: E402
from hub.db import connect  # noqa: E402
from hub.pipeline import run  # noqa: E402
from hub.recommender import PartsRecommender  # noqa: E402
from hub.service import IntegrationHub  # noqa: E402

GITHUB_URL = "https://github.com/AlexaGonzalesTuesta/equipment-parts-integration-hub"
BASE_DB = Path(os.environ.get("HUB_DB_PATH", C.DB_PATH))
SANDBOX_DIR = Path(tempfile.gettempdir()) / "parts-hub-sandboxes"
SANDBOX_MAX_AGE_HOURS = 6

INK, MUTED, LINE, ACCENT = "#1d1d1b", "#5f5e58", "#d4d1c8", "#b86e00"
OK, INFO, BAD, GREY = "#2f6f4e", "#35577a", "#a63a2b", "#7a7870"

st.set_page_config(page_title="Parts Order Integration Hub", layout="wide", initial_sidebar_state="collapsed")


def raw(body):
    """Render raw HTML or CSS. st.html never runs it through the Markdown parser."""
    return st.html(body)


def _stretch():
    """Full-width keyword for this Streamlit version (newer versions use width=, older use_container_width=)."""
    major, minor = (int(x) for x in st.__version__.split(".")[:2])
    return {"width": "stretch"} if (major, minor) >= (1, 46) else {"use_container_width": True}


STRETCH = _stretch()
# st.plotly_chart gained a width= parameter later than buttons and tables did
CHART = ({"width": "stretch"} if "width" in inspect.signature(st.plotly_chart).parameters
         else {"use_container_width": True})

# ----------------------------------------------------------------------------- style
raw(
    """
<style>
@import url('https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500&family=IBM+Plex+Sans:wght@400;500;600&display=swap');
html, body, p, li, td, th, label, input, textarea, select, button, h1, h2, h3,
[data-testid="stMarkdownContainer"], [data-testid="stMetricValue"], [data-baseweb="tab"] {
  font-family: 'IBM Plex Sans', 'Segoe UI', Helvetica, Arial, sans-serif;
}
code, .mono { font-family: 'IBM Plex Mono', Consolas, 'Courier New', monospace; }
header[data-testid="stHeader"] { background: transparent; }
.block-container { padding-top: 1.6rem; max-width: 1280px; }

.topbar { display:flex; justify-content:space-between; align-items:flex-end; gap:24px;
  border-bottom:2px solid #1d1d1b; padding-bottom:10px; margin-bottom:14px; }
.eyebrow { font-size:12px; letter-spacing:.08em; text-transform:uppercase; color:#b86e00; font-weight:600; }
.topbar h1 { font-size:28px; font-weight:600; margin:2px 0 0 0; padding:0; color:#1d1d1b; line-height:1.2; }
.systems { font-family:'IBM Plex Mono', Consolas, monospace; font-size:12.5px; color:#5f5e58; margin:4px 0 0 0; }
.meta { text-align:right; font-size:12.5px; color:#5f5e58; line-height:1.6; }
.meta a { color:#35577a; }
.lead { font-size:17px; color:#1d1d1b; margin:4px 0 14px 0; max-width:900px; }
.lead b { font-weight:600; }

[data-testid="stMetric"] { background:#ffffff; border:1px solid #d4d1c8; border-radius:2px; padding:10px 14px; }
[data-testid="stMetricLabel"] p { font-size:13px; color:#5f5e58; white-space:normal; line-height:1.3; }
[data-testid="stMetricLabel"] div { overflow:visible; }
[data-testid="stMetricValue"] { font-size:30px; font-weight:600; color:#1d1d1b; }
.kpi-note { font-size:12px; color:#5f5e58; margin:6px 0 4px 0; }

.stTabs [data-baseweb="tab-list"] { gap:0; border-bottom:1px solid #d4d1c8; }
.stTabs [data-baseweb="tab"] { padding:8px 18px; font-weight:500; }
.stTabs [aria-selected="true"] { color:#1d1d1b; }
.stTabs [data-baseweb="tab-highlight"] { background-color:#b86e00; }

.stButton button, .stFormSubmitButton button { width:100%; border-radius:2px; border:1px solid #bdb9ae;
  background:#ffffff; color:#1d1d1b; justify-content:flex-start; text-align:left; font-weight:500;
  transition: background .12s, border-color .12s; }
.stButton button:hover, .stFormSubmitButton button:hover { border-color:#b86e00; background:#fbf6ee; color:#1d1d1b; }
.stButton button:focus-visible, .stFormSubmitButton button:focus-visible,
input:focus-visible, textarea:focus-visible { outline:2px solid #b86e00 !important; outline-offset:1px; }
.stButton button:active { background:#f3e8d6; }
.scn-desc { font-size:12.5px; color:#5f5e58; margin:-6px 0 10px 2px; line-height:1.35; }
.panel-title { font-size:12px; letter-spacing:.08em; text-transform:uppercase; color:#5f5e58; font-weight:600;
  margin:4px 0 8px 0; }

.banner { border:1px solid #d4d1c8; border-left:5px solid #7a7870; background:#ffffff; padding:10px 14px;
  margin-bottom:12px; font-size:15px; }
.banner.ok { border-left-color:#2f6f4e; } .banner.info { border-left-color:#35577a; }
.banner.warn { border-left-color:#b86e00; } .banner.bad { border-left-color:#a63a2b; }
.banner .kicker { font-size:12px; font-weight:600; letter-spacing:.06em; text-transform:uppercase; }
.banner.ok .kicker { color:#2f6f4e; } .banner.info .kicker { color:#35577a; }
.banner.warn .kicker { color:#b86e00; } .banner.bad .kicker { color:#a63a2b; }

.doc { background:#ffffff; border:1px solid #d4d1c8; padding:12px 14px; margin-bottom:12px; }
.doc-grid { display:grid; grid-template-columns: repeat(3, minmax(0,1fr)); gap:8px 18px; margin-bottom:10px; }
.doc-grid .k { font-size:11.5px; color:#5f5e58; text-transform:uppercase; letter-spacing:.05em; }
.doc-grid .v { font-size:14px; color:#1d1d1b; }
table.lines { width:100%; border-collapse:collapse; font-size:13.5px; }
table.lines th { text-align:left; font-weight:600; color:#5f5e58; font-size:12px; border-bottom:1px solid #1d1d1b;
  padding:5px 6px; }
table.lines td { border-bottom:1px solid #e6e3db; padding:6px; vertical-align:top; }
table.lines td.num { text-align:right; font-variant-numeric: tabular-nums; }
table.lines tr:hover td { background:#faf8f3; }
.mono { font-size:13px; white-space:nowrap; }
.chip { display:inline-block; font-size:11.5px; font-weight:600; padding:1px 7px; border:1px solid; border-radius:2px;
  white-space:nowrap; }
.chip.ok { color:#2f6f4e; border-color:#9cc3ad; background:#f1f7f3; }
.chip.info { color:#35577a; border-color:#a8bccf; background:#f1f5f9; }
.chip.warn { color:#8a5300; border-color:#e0c08a; background:#fbf5ea; }
.chip.bad { color:#a63a2b; border-color:#ddaaa2; background:#fbf1ef; }
.chip.grey { color:#5f5e58; border-color:#cfccc3; background:#f6f5f2; }
ol.steps { margin:0; padding-left:20px; font-size:13.5px; }
ol.steps li { margin:0 0 6px 0; }
ol.steps .d { color:#5f5e58; font-size:12.5px; }
.empty { border:1px dashed #bdb9ae; background:#fbfaf7; padding:28px 18px; color:#5f5e58; text-align:center; }
.owner-strip { display:flex; flex-wrap:wrap; gap:8px; margin:4px 0 12px 0; }
.owner { border:1px solid #d4d1c8; background:#ffffff; padding:6px 10px; font-size:13px; }
.owner b { font-size:16px; margin-right:4px; }
.small { font-size:12.5px; color:#5f5e58; }
</style>
""")

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


# ----------------------------------------------------------------------------- data and sandbox
SNAPSHOT_DIR = ROOT / "app" / "snapshot"


@st.cache_resource(show_spinner="Loading the simulated ERP, dealer inventory and telematics data...")
def base_landscape(db_path: str):
    """Make sure the base database exists, then train the AI model on its service notes.

    On the free host the app sleeps and restarts often. Rebuilding the 90-day simulation on
    the first visit keeps the CPU busy long enough that the page's own files time out, so the
    app restores the bundled snapshot instead and only runs the pipeline if there is none."""
    path = Path(db_path)
    if not path.exists():
        snap = SNAPSHOT_DIR / "hub.db.gz"
        if snap.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            with gzip.open(snap, "rb") as src, open(path, "wb") as dst:
                shutil.copyfileobj(src, dst)
            shutil.copy(SNAPSHOT_DIR / "run_summary.json", path.parent / "run_summary.json")
        else:
            run(path, quiet=True)
    conn = connect(db_path)
    rows = conn.execute("SELECT note_text, fault_code FROM ml_history_notes").fetchall()
    conn.close()
    # Accuracy figures come from the pipeline run, so the app skips the extra evaluation fits.
    return PartsRecommender().fit([r["note_text"] for r in rows], [r["fault_code"] for r in rows], evaluate=False)


def _clean_old_sandboxes():
    SANDBOX_DIR.mkdir(parents=True, exist_ok=True)
    cutoff = time.time() - SANDBOX_MAX_AGE_HOURS * 3600
    for f in SANDBOX_DIR.glob("*.db*"):
        try:
            if f.stat().st_mtime < cutoff:
                f.unlink()
        except OSError:
            pass


def new_sandbox():
    """Copy the base database into a private file for this visitor."""
    _clean_old_sandboxes()
    path = SANDBOX_DIR / f"{uuid.uuid4().hex}.db"
    src = sqlite3.connect(str(BASE_DB))
    dst = sqlite3.connect(str(path))
    src.backup(dst)
    src.close()
    dst.close()
    return path


def sandbox_conn():
    path = st.session_state.get("sandbox_path")
    if path is None or not Path(path).exists():
        path = new_sandbox()
        st.session_state.sandbox_path = path
        st.session_state.sandbox_id = uuid.uuid4().hex[:4].upper()
        st.session_state.sent = 0
        st.session_state.used_machines = []
        st.session_state.last = None
    if st.session_state.get("conn_path") != path:
        st.session_state.conn = connect(path)
        st.session_state.conn_path = path
    return st.session_state.conn


def reset_sandbox():
    old = st.session_state.get("sandbox_path")
    conn = st.session_state.get("conn")
    if conn is not None:
        conn.close()
    for key in ("sandbox_path", "conn", "conn_path", "last"):
        st.session_state.pop(key, None)
    if old:
        for f in Path(old).parent.glob(Path(old).name + "*"):
            try:
                f.unlink()
            except OSError:
                pass


@st.cache_data(show_spinner=False)
def baseline(db_path: str):
    """90-day results from the untouched base database (identical for every visitor)."""
    conn = connect(db_path)
    summary_path = Path(db_path).parent / "run_summary.json"
    s = json.loads(summary_path.read_text()) if summary_path.exists() else M.summary(conn)
    orders = M.orders(conn)
    dl = M.dead_letters(conn)
    retries = pd.read_sql("SELECT detail FROM hub_audit WHERE status = 'RETRY'", conn)
    conn.close()
    return s, orders, dl, retries


# ----------------------------------------------------------------------------- scenario helpers
def now():
    return datetime.now().replace(microsecond=0)


def next_id(prefix):
    st.session_state.sent += 1
    return f"{prefix}-{st.session_state.sandbox_id}-{st.session_state.sent:03d}"


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


def machines(conn, machine_type=None):
    sql = ("SELECT m.machine_id, m.model, m.machine_type, m.dealer_id, d.region FROM erp_machines m "
           "JOIN erp_dealers d USING (dealer_id)")
    rows = conn.execute(sql + (" WHERE m.machine_type = ?" if machine_type else "") + " ORDER BY m.machine_id",
                        (machine_type,) if machine_type else ()).fetchall()
    used = set(st.session_state.used_machines)
    return [r for r in rows if r["machine_id"] not in used]


def has_open_order(conn, machine_id, fault):
    since = (now() - timedelta(days=7)).isoformat()
    return ERPConnector(conn).open_order_for_fault(machine_id, fault, since) is not None


def region_alt(conn, m, pn, qty):
    for d in ERPConnector(conn).dealers_in_region(m["region"], m["dealer_id"]):
        if avail(conn, d, pn) >= qty:
            return d
    return None


def find_in_stock(conn, faults, machine_type=None):
    for m in machines(conn, machine_type):
        for f in faults:
            if not has_open_order(conn, m["machine_id"], f) and all(
                    avail(conn, m["dealer_id"], pn) >= q for pn, q in kit(conn, f)):
                return m, f
    return None, None


def find_transfer(conn, faults):
    """A machine whose own dealer lacks a part that another dealer in the region has."""
    fallback = None
    for m in machines(conn):
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


def run_scenario(key, conn, model):
    note_for = {f: n for f, n in [("COO-401", "engine overheating, coolant temp in red"),
                                  ("HYD-130", "hose burst on stick, spraying hydraulic oil"),
                                  ("ENG-230", "air filter restriction indicator red"),
                                  ("UND-620", "roller leaking oil")]}
    extra = {}
    if key in ("assigned", "duplicate", "outage"):
        m, f = find_in_stock(conn, ["HYD-130", "COO-401", "ENG-230", "UND-620"],
                             "Excavator" if key == "assigned" else None)
        if m is None:
            raise RuntimeError("No machine with parts in stock is left in this sandbox. Reset the sandbox.")
        payload = telematics_payload(next_id("EV"), m["machine_id"], f, note_for.get(f, ""))
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
        m, f, forced = find_transfer(conn, ["COO-401", "HYD-130", "UND-620", "ENG-230", "ELE-501", "FUE-310"])
        if m is None:
            raise RuntimeError("No regional stock is left in this sandbox. Reset the sandbox.")
        payload = telematics_payload(next_id("EV"), m["machine_id"], f, note_for.get(f, ""))
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
        m = next((m for m in machines(conn) if not has_open_order(conn, m["machine_id"], fault)), None)
        if m is None:
            raise RuntimeError("No machines left in this sandbox. Reset the sandbox.")
        payload = telematics_payload(next_id("EV"), m["machine_id"], None, note)
        reliable_hub(conn, model).process_telematics(payload)
        mid, channel = payload["event_id"], "TELEMATICS"
    elif key == "price":
        cust = conn.execute("SELECT c.customer_id, d.dealer_id FROM erp_customers c JOIN erp_dealers d "
                            "ON d.region = c.region ORDER BY c.customer_id LIMIT 1 OFFSET ?",
                            (st.session_state.sent % 50,)).fetchone()
        part = conn.execute("SELECT part_number, list_price FROM erp_parts WHERE description LIKE 'Primary air filter'"
                            ).fetchone() or conn.execute("SELECT part_number, list_price FROM erp_parts LIMIT 1").fetchone()
        payload = {"web_order_id": next_id("WEB"), "customer_id": cust["customer_id"], "dealer_id": cust["dealer_id"],
                   "submitted_at": now().isoformat(),
                   "lines": [{"part_number": part["part_number"], "quantity": 2,
                              "unit_price": round(part["list_price"] * 0.88, 2)}]}
        reliable_hub(conn, model).process_web_order(payload)
        mid, channel = payload["web_order_id"], "ECOMMERCE"
        m = None
    else:
        raise ValueError(key)
    if m is not None:
        st.session_state.used_machines.append(m["machine_id"])
    st.session_state.last = {"scenario": key, "message_id": mid, "channel": channel, **extra}


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


def banner(kind, kicker, text):
    raw(f'<div class="banner {kind}"><div class="kicker">{esc(kicker)}</div>{text}</div>')


def render_result(conn, model):
    last = st.session_state.get("last")
    if not last:
        raw('<div class="empty">No event sent yet.<br>Pick a scenario on the left. The ERP order, the dealer '
                    'that ships each part, and every step the hub took will appear here.</div>')
        return
    mid = last["message_id"]
    res = conn.execute("SELECT * FROM hub_results WHERE message_id = ? ORDER BY rowid DESC LIMIT 1", (mid,)).fetchone()
    if res is None:
        st.error("This message was not recorded. Reset the sandbox and try again.")
        return
    outcome, detail = res["outcome"], res["detail"] or ""
    secs = f'Processed in {res["latency_ms"] / 1000:.3f} seconds.'

    if outcome == "ORDER_CREATED":
        o = conn.execute("SELECT fulfillment, status FROM erp_orders WHERE order_id = ?", (res["order_id"],)).fetchone()
        text = {"ASSIGNED_DEALER": "Every part is reserved at the machine's own dealer. No phone call, no re-keying.",
                "REGIONAL_TRANSFER": "The assigned dealer was short, so the hub reserved those parts at another "
                                     "dealer in the same region.",
                "BACKORDER": "Some parts are on backorder. The dealer sees it now instead of after a phone call."
                }[o["fulfillment"]]
        if last.get("scenario") == "outage":
            text = "Replayed after the dealer system came back. " + text
        banner("ok", f"Order {res['order_id']} created", f"{text} {secs}")
        if last.get("forced_part"):
            st.caption(f"In your sandbox, the assigned dealer's stock of {last['forced_part']} was set to zero to "
                       "create this situation.")
        raw(render_order(conn, res["order_id"]))
    elif outcome == "DUPLICATE":
        banner("info", "Resend blocked",
               f"Order {esc(last.get('first_order'))} already covers this event, so the customer is not billed twice. "
               f"The database also refuses a second order for the same message. {secs}")
        if last.get("first_order"):
            raw(render_order(conn, last["first_order"]))
    elif outcome == "MANUAL_REVIEW":
        r = conn.execute("SELECT * FROM hub_manual_review WHERE message_id = ?", (mid,)).fetchone()
        top3 = json.loads(r["top3"]) if r else []
        descs = {x["fault_code"]: x["description"] for x in conn.execute("SELECT fault_code, description FROM erp_fault_catalog")}
        guesses = "".join(f'<tr><td class="mono">{esc(c)}</td><td>{esc(descs.get(c, ""))}</td>'
                          f'<td class="num">{p:.0%}</td></tr>' for c, p in top3)
        banner("warn", "Sent to a service advisor",
               f"The AI was only {r['confidence']:.0%} sure, below the {C.AI_CONFIDENCE_THRESHOLD:.0%} bar, so no "
               f"parts were ordered. A wrong guess would ship the wrong parts to a job site. {secs}")
        raw(f'<div class="doc"><div class="small">Operator note</div><div style="margin:2px 0 10px 0">'
                    f'"{esc(r["symptom_note"])}"</div><table class="lines"><thead><tr><th>AI guess</th><th>Fault</th>'
                    f'<th style="text-align:right">Confidence</th></tr></thead><tbody>{guesses}</tbody></table></div>')
    elif outcome in ("DEAD_LETTER", "REJECTED"):
        code = detail.split(":")[0]
        what, owner, step = REASONS.get(code, (detail, "IT support", "Investigate"))
        banner("bad", f"Held for {owner}", f"{esc(what)}. Nothing was half-ordered. Next step: {esc(step)}. {secs}")
        raw(f'<div class="small">System detail: <span class="mono">{esc(detail)}</span></div>')
        open_dl = conn.execute("SELECT 1 FROM hub_dead_letter WHERE message_id = ? AND replayable = 1 "
                               "AND resolved_at IS NULL", (mid,)).fetchone()
        if code == "DMS_UNAVAILABLE" and open_dl:
            if st.button("Dealer system is back up: replay this order", key="replay_last"):
                reliable_hub(conn, model).replay_one(mid)
                st.rerun()
    else:
        banner("info", OUTCOME_CHIP.get(outcome, (outcome, ""))[0], f"{esc(detail)} {secs}")

    with st.expander("Every step the hub took", expanded=outcome != "ORDER_CREATED"):
        raw(f'<div class="small" style="margin-bottom:6px">Message <span class="mono">{esc(mid)}</span>, '
                    f'{esc(last["channel"].title())}</div>' + render_steps(conn, mid))


def style_fig(fig, height=300):
    fig.update_layout(height=height, margin=dict(l=8, r=8, t=30, b=8), plot_bgcolor="#ffffff",
                      legend=dict(orientation="h", yanchor="bottom", y=1.0, x=0),
                      paper_bgcolor="rgba(0,0,0,0)", legend_title_text="",
                      font=dict(family="IBM Plex Sans, Segoe UI, Arial", size=12, color=INK),
                      title_font=dict(size=14), hoverlabel=dict(font_family="IBM Plex Sans, Segoe UI, Arial"))
    fig.update_xaxes(gridcolor="#ece9e2", linecolor=LINE, title=None)
    fig.update_yaxes(gridcolor="#ece9e2", linecolor=LINE, title=None)
    return fig


# ----------------------------------------------------------------------------- page
model = base_landscape(str(BASE_DB))
try:
    conn = sandbox_conn()
except Exception as exc:  # noqa: BLE001
    st.error(f"Could not create your sandbox: {exc}. Refresh the page to try again.")
    st.stop()
s, base_orders, base_dl, base_retries = baseline(str(BASE_DB))

raw(
    f"""
<div class="topbar">
  <div>
    <div class="eyebrow">Dealer parts operations</div>
    <h1>Parts Order Integration Hub</h1>
    <p class="systems">Telematics &rarr; Hub &rarr; ERP sales order &middot; Dealer inventory &middot; Storefront</p>
  </div>
  <div class="meta">Built by Alexa Gonzales-Tuesta &middot; <a href="{GITHUB_URL}" target="_blank">Source on GitHub</a><br>
  Machines, dealers, customers and part numbers are fictional</div>
</div>
<p class="lead">Pick a scenario to watch a <b>machine fault become a reserved ERP parts order in under a second</b>,
with no phone call or re-keying, and see what happens when something goes wrong.</p>
""")

mix = s.get("fulfillment_mix", {})
filled = (mix.get("ASSIGNED_DEALER", 0) + mix.get("REGIONAL_TRANSFER", 0)) / max(sum(mix.values()), 1)
dms_total = int((base_dl.reason_code == "DMS_UNAVAILABLE").sum())
dms_fixed = int(((base_dl.reason_code == "DMS_UNAVAILABLE") & base_dl.resolved_at.notna()).sum())
dup_orders = int(base_orders.source_message_id.duplicated().sum())
k = st.columns(4)
k[0].metric("Faults ordered with no phone call", f"{s['telematics_touchless_rate']:.0%}",
            help="Actionable fault events that became an ERP parts order with no person involved.")
k[1].metric("Orders filled from dealer stock", f"{filled:.0%}",
            help="The machine's own dealer or another dealer in the same region had every part.")
k[2].metric(f"Duplicate orders from {s['duplicates_blocked']} resends", f"{dup_orders}",
            help="Resent messages were recognized and blocked.")
k[3].metric("Outage orders auto-recovered", f"{dms_fixed} of {dms_total}",
            help="Orders held during two dealer system outages, replayed with no manual work.")
raw(f'<div class="kpi-note">Simulated 90-day run: {s["messages_received"]:,} inbound messages, 12 dealers, '
            f'1,500 machines, {s["orders_created"]:,} ERP orders worth ${s["order_value_total"] / 1e6:.1f}M.</div>')

tabs = st.tabs(["Try it", "Exceptions queue", "90-day results", "Requirements and tests"])

# ----------------------------------------------------------------------------- try it
with tabs[0]:
    left, right = st.columns([1, 1.9], gap="large")
    with left:
        raw('<div class="panel-title">Scenarios</div>')
        for key, label, desc in SCENARIOS:
            if st.button(label, key=f"scn_{key}", **STRETCH):
                try:
                    with st.spinner("Sending the event to the hub..."):
                        run_scenario(key, conn, model)
                except Exception as exc:  # noqa: BLE001
                    st.session_state.last = None
                    st.error(f"That scenario could not run: {exc}")
            raw(f'<div class="scn-desc">{esc(desc)}</div>')

        with st.expander("Send your own fault event"):
            mrows = conn.execute("SELECT machine_id, model, machine_type FROM erp_machines ORDER BY machine_id "
                                 "LIMIT 300").fetchall()
            faults = conn.execute("SELECT fault_code, description FROM erp_fault_catalog ORDER BY fault_code").fetchall()
            fault_desc = {f["fault_code"]: f["description"] for f in faults}
            with st.form("own_event"):
                m_id = st.selectbox("Machine", [r["machine_id"] for r in mrows],
                                    format_func=lambda x: next(f"{x}  {r['model']} {r['machine_type']}"
                                                               for r in mrows if r["machine_id"] == x))
                code = st.selectbox("Fault code", [""] + list(fault_desc),
                                    format_func=lambda x: f"{x}  {fault_desc[x]}" if x else "None, let the AI read the note")
                sev = st.selectbox("Severity", [3, 2, 1], format_func=lambda x: {3: "3  Critical", 2: "2  Warning",
                                                                                   1: "1  Information only"}[x])
                note = st.text_input("Operator note", "engine overheating, coolant temp in red", max_chars=200)
                sent = st.form_submit_button("Send event to the hub")
            if sent:
                try:
                    payload = telematics_payload(next_id("EV"), m_id, code or None, note, sev)
                    reliable_hub(conn, model).process_telematics(payload)
                    st.session_state.last = {"scenario": "own", "message_id": payload["event_id"],
                                             "channel": "TELEMATICS"}
                except Exception as exc:  # noqa: BLE001
                    st.error(f"The event could not be processed: {exc}")

        raw('<div class="panel-title" style="margin-top:18px">Your sandbox</div>')
        raw(f'<div class="small">You have sent {st.session_state.sent} event(s). Changes stay in your own '
                    'copy of the data.</div>')
        if st.button("Reset my sandbox", key="reset"):
            reset_sandbox()
            st.rerun()

    with right:
        raw('<div class="panel-title">Result</div>')
        render_result(conn, model)

# ----------------------------------------------------------------------------- exceptions
with tabs[1]:
    raw('<p class="lead" style="font-size:15px">Everything the hub could not finish on its own, with the team '
                'that owns it. Nothing is silently dropped. This queue includes anything you created in your '
                'sandbox.</p>')
    dl = pd.read_sql("SELECT * FROM hub_dead_letter WHERE resolved_at IS NULL ORDER BY id DESC", conn)
    review = pd.read_sql("SELECT * FROM hub_manual_review WHERE status = 'OPEN' ORDER BY id DESC", conn)
    for frame in (dl, review):
        frame["created_at"] = pd.to_datetime(frame["created_at"]).dt.strftime("%b %d, %H:%M")
    dl["problem"] = dl.reason_code.map(lambda c: REASONS.get(c, (c, "", ""))[0])
    dl["owner"] = dl.reason_code.map(lambda c: REASONS.get(c, ("", "IT support", ""))[1])
    dl["next_step"] = dl.reason_code.map(lambda c: REASONS.get(c, ("", "", "Investigate"))[2])
    counts = dl.owner.value_counts().to_dict()
    counts["Service advisors"] = len(review)
    raw('<div class="owner-strip">' + "".join(
        f'<div class="owner"><b>{n}</b>{esc(o)}</div>' for o, n in sorted(counts.items(), key=lambda x: -x[1])) +
        "</div>")

    held = dl[dl.reason_code == "DMS_UNAVAILABLE"]
    raw('<div class="panel-title">Waiting on a dealer system</div>')
    if held.empty:
        raw('<div class="small">Nothing is waiting on a dealer system. Run "Dealer system is down" on the '
                    'Try it tab to see one held and replayed.</div>')
    for _, r in held.head(5).iterrows():
        c1, c2 = st.columns([3, 1])
        c1.html(f'<span class="mono">{esc(r.message_id)}</span> &middot; {esc(r.detail)}')
        if c2.button("Replay", key=f"replay_{r.message_id}"):
            reliable_hub(conn, model).replay_one(r.message_id)
            st.rerun()

    raw('<div class="panel-title" style="margin-top:16px">Held for a person</div>')
    owners = ["All teams"] + sorted(dl.owner.unique())
    pick = st.selectbox("Team", owners, label_visibility="collapsed")
    view = dl if pick == "All teams" else dl[dl.owner == pick]
    if view.empty:
        raw('<div class="empty">No open exceptions for this team.</div>')
    else:
        st.dataframe(view[["created_at", "message_id", "channel", "problem", "owner", "next_step", "detail"]]
                     .rename(columns={"created_at": "received", "next_step": "next step"}),
                     hide_index=True, **STRETCH)

    raw('<div class="panel-title" style="margin-top:16px">Service advisor queue (AI not confident)</div>')
    if review.empty:
        raw('<div class="empty">No notes waiting for an advisor.</div>')
    else:
        descs = dict(conn.execute("SELECT fault_code, description FROM erp_fault_catalog").fetchall())
        review["ai_best_guess"] = review.predicted_fault.map(lambda c: f"{c}  {descs.get(c, '')}")
        st.dataframe(review[["created_at", "message_id", "machine_id", "symptom_note", "ai_best_guess", "confidence"]]
                     .rename(columns={"created_at": "received", "symptom_note": "operator note",
                                      "ai_best_guess": "AI best guess"}),
                     hide_index=True, column_config={"confidence": st.column_config.NumberColumn(format="%.2f")},
                     **STRETCH)

    raw('<div class="panel-title" style="margin-top:16px">Trace a message</div>')
    default_id = (st.session_state.get("last") or {}).get("message_id", "")
    mid = st.text_input("Message id", value=default_id, placeholder="Event id or web order id",
                        label_visibility="collapsed")
    if mid:
        inbound = conn.execute("SELECT * FROM hub_inbound WHERE message_id = ?", (mid.strip(),)).fetchone()
        if inbound is None:
            raw('<div class="small">The hub never received this id. The problem is upstream, in the sending '
                        'system or the network.</div>')
        else:
            raw(f'<div class="small" style="margin-bottom:6px">Final status: '
                        f'{chip(*OUTCOME_CHIP.get(inbound["status"], (inbound["status"], "grey")))}</div>'
                        + render_steps(conn, mid.strip()))
    else:
        raw('<div class="small">Enter an event id or web order id to see every step the hub took.</div>')

# ----------------------------------------------------------------------------- 90-day results
with tabs[2]:
    o = base_orders.copy()
    o["week"] = pd.to_datetime(o["created_at"]).dt.to_period("W").dt.start_time
    o["source"] = o.channel.map({"TELEMATICS": "Machine fault (telematics)", "ECOMMERCE": "Storefront order"})
    last_day = pd.to_datetime(o["created_at"]).max().normalize()
    first_day = pd.to_datetime(o["created_at"]).min().normalize()
    o = o[(o["week"] >= first_day) & (o["week"] + pd.Timedelta(days=6) <= last_day)]  # full weeks only
    wk = o.groupby(["week", "source"]).size().reset_index(name="orders")
    by_dealer = o.groupby(["dealer_name", "region"]).agg(
        orders=("order_id", "count"),
        own_stock=("fulfillment", lambda x: (x == "ASSIGNED_DEALER").mean()),
        transfer=("fulfillment", lambda x: (x == "REGIONAL_TRANSFER").mean()),
        backorder=("fulfillment", lambda x: (x == "BACKORDER").mean()),
        value=("total_value", "sum")).reset_index().sort_values("backorder", ascending=False)
    worst = by_dealer.iloc[0]
    for col in ("own_stock", "transfer", "backorder"):
        by_dealer[col] = (100 * by_dealer[col]).round(1)

    c1, c2 = st.columns([1, 1], gap="large")
    with c1:
        raw('<div class="panel-title">ERP orders created per week</div>')
        fig = px.line(wk, x="week", y="orders", color="source", markers=True,
                      color_discrete_map={"Machine fault (telematics)": INK, "Storefront order": ACCENT})
        fig.update_traces(line_width=2, marker_size=6)
        st.plotly_chart(style_fig(fig), **CHART)
    with c2:
        raw('<div class="panel-title">Where to add stock</div>')
        raw(f'<div class="small" style="margin-bottom:8px">{esc(worst.dealer_name)} backorders '
                    f'{worst.backorder:.0%} of its orders, the highest of 12 dealers. Its parts mix is the first place '
                    'to raise stocking levels.</div>')
        st.dataframe(by_dealer.drop(columns=["region", "value"]).rename(columns={"dealer_name": "dealer", "own_stock": "own stock",
                                               "backorder": "backordered"}),
                     hide_index=True, height=300,
                     column_config={"own stock": st.column_config.NumberColumn(format="%.0f%%"),
                                    "transfer": st.column_config.NumberColumn(format="%.0f%%"),
                                    "backordered": st.column_config.NumberColumn(format="%.0f%%")},
                     **STRETCH)

    c3, c4 = st.columns([1.25, 1], gap="large")
    with c3:
        raw('<div class="panel-title">Exceptions in 90 days, by owner</div>')
        ex = base_dl.assign(problem=base_dl.reason_code.map(lambda c: REASONS.get(c, (c, "", ""))[0]),
                            owner=base_dl.reason_code.map(lambda c: REASONS.get(c, ("", "IT support", ""))[1]),
                            resolved=base_dl.resolved_at.notna())
        ex = ex.groupby(["problem", "owner"]).agg(messages=("message_id", "count"),
                                                  recovered=("resolved", "sum")).reset_index()
        st.dataframe(ex.sort_values("messages", ascending=False), hide_index=True, **STRETCH)
        raw(f'<div class="small">{len(base_retries):,} dealer system timeouts were retried automatically '
                    'before any of these were held.</div>')
    with c4:
        curve = pd.DataFrame(s.get("ai_threshold_curve", []))
        if curve.empty:
            raw('<div class="empty">AI evaluation not available. Run python run_pipeline.py.</div>')
        else:
            long = curve.melt("threshold", var_name="measure", value_name="share")
            long["measure"] = long["measure"].map({"auto_order_coverage": "Notes ordered automatically",
                                                   "auto_order_precision": "Automatic orders that were right"})
            raw('<div class="panel-title">Setting the AI confidence bar</div>')
            fig = px.line(long, x="threshold", y="share", color="measure", markers=True,
                          color_discrete_sequence=[INK, ACCENT])
            fig.update_traces(line_width=2, marker_size=6)
            fig.update_yaxes(tickformat=".0%", range=[0, 1.02])
            fig.add_vline(x=C.AI_CONFIDENCE_THRESHOLD, line_dash="dash", line_color=GREY,
                          annotation_text=f"chosen {C.AI_CONFIDENCE_THRESHOLD:.0%}")
            st.plotly_chart(style_fig(fig, 280), **CHART)
            off = s.get("ai_offline_eval", {})
            if off:
                raw(f'<div class="small">At 65%, {off["auto_order_precision"]:.0%} of automatic orders are '
                            f'right and {off["auto_order_coverage"]:.0%} of notes need no advisor. A higher bar means '
                            'fewer wrong parts but more manual work.</div>')

# ----------------------------------------------------------------------------- requirements
with tabs[3]:
    rtm_path = ROOT / "docs" / "requirements_traceability.csv"
    if rtm_path.exists():
        rtm = pd.read_csv(rtm_path)
        covered = int((rtm["tests_passed"] > 0).sum())
        lat = s.get("latency", {})
        raw(f'<p class="lead" style="font-size:15px"><b>{covered} of {len(rtm)}</b> business and technical '
                    'requirements are verified by automated tests that run on every change. Processing time is '
                    f'{lat.get("p95_ms", 0):.1f} ms at the 95th percentile against a {C.P95_LATENCY_TARGET_MS} ms '
                    'target (NFR-01).</p>')
        st.dataframe(rtm[["req_id", "priority", "requirement", "test_levels", "tests_passed", "status"]]
                     .rename(columns={"req_id": "id", "test_levels": "test levels", "tests_passed": "tests passed"}),
                     hide_index=True, **STRETCH)
    else:
        raw('<div class="empty">Run pytest, then python tools/build_rtm.py, to build the traceability '
                    'matrix.</div>')
    raw(f'<div class="small">Business requirements, solution design, process flows, test plan and the '
                f'troubleshooting runbook are in the <a href="{GITHUB_URL}/tree/main/docs" target="_blank">docs '
                'folder</a>.</div>')
