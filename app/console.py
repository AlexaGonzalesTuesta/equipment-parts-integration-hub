"""Parts Order Integration Hub: dealer parts operations console.

Run:  streamlit run app/console.py

Every visitor works in a private copy of the database (a sandbox), so scenarios and
the reset button only change that visitor's data.
"""
import gzip
import inspect
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import time
import uuid
from pathlib import Path

import pandas as pd
import plotly.express as px
import streamlit as st

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from hub import config as C  # noqa: E402
from hub import metrics as M  # noqa: E402
from hub.db import connect  # noqa: E402
from hub.pipeline import run  # noqa: E402
from hub.recommender import PartsRecommender  # noqa: E402
from app.demo import (OUTCOME_CHIP, REASONS, SCENARIOS, chip, esc, next_id, reliable_hub,  # noqa: E402
                      render_steps, result_parts, run_scenario, telematics_payload)

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


# ----------------------------------------------------------------------------- rendering
def banner(kind, kicker, text):
    raw(f'<div class="banner {kind}"><div class="kicker">{esc(kicker)}</div>{text}</div>')


def render_result(conn, model):
    last = st.session_state.get("last")
    if not last:
        raw('<div class="empty">No event sent yet.<br>Pick a scenario on the left. The ERP order, the dealer '
            'that ships each part, and every step the hub took will appear here.</div>')
        return
    parts = result_parts(conn, last)
    if parts is None:
        st.error("This message was not recorded. Reset the sandbox and try again.")
        return
    banner(parts["kind"], parts["kicker"], parts["text"])
    if parts["caption"]:
        st.caption(parts["caption"])
    if parts["body"]:
        raw(parts["body"])
    if parts["detail"]:
        raw(parts["detail"])
    if parts["replayable"]:
        if st.button("Dealer system is back up: replay this order", key="replay_last"):
            reliable_hub(conn, model).replay_one(last["message_id"])
            st.rerun()
    with st.expander("Every step the hub took", expanded=parts["outcome"] != "ORDER_CREATED"):
        raw(parts["steps"])


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
                        st.session_state.last = run_scenario(key, conn, model, st.session_state)
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
                    payload = telematics_payload(next_id(st.session_state, "EV"), m_id, code or None, note, sev)
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
