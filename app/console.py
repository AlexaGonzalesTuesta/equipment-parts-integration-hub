"""Integration Operations Console.  Run:  streamlit run app/console.py"""
import json
import os
import sys
from pathlib import Path

import pandas as pd
import plotly.express as px
import streamlit as st

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from hub import config as C  # noqa: E402
from hub.db import connect  # noqa: E402
from hub import metrics as M  # noqa: E402
from hub.pipeline import run  # noqa: E402
from hub.recommender import train_from_db  # noqa: E402
from hub.service import IntegrationHub  # noqa: E402

GITHUB_URL = "https://github.com/AlexaGonzalesTuesta/equipment-parts-integration-hub"
LINKEDIN_URL = "https://www.linkedin.com/in/alexa-gonzales-tuesta/"
BLUE, ORANGE, AQUA, INK_2 = "#2a78d6", "#eb6834", "#1baf7a", "#8a8984"
CHANNEL_COLORS = {"TELEMATICS": BLUE, "ECOMMERCE": ORANGE}
DB_PATH = Path(os.environ.get("HUB_DB_PATH", C.DB_PATH))

st.set_page_config(page_title="Equipment Parts Integration Hub", layout="wide")


@st.cache_resource(show_spinner="First run: building the simulated systems and processing 3,800 messages ...")
def get_hub(db_path: str):
    if not Path(db_path).exists():
        run(Path(db_path), quiet=True)
    conn = connect(db_path)
    return conn, IntegrationHub(conn, train_from_db(conn))


def style(fig, height=320):
    if fig.data and fig.data[0].type == "bar" and fig.data[0].orientation == "h":
        fig.update_yaxes(categoryorder="total ascending")
    fig.update_layout(height=height, margin=dict(l=10, r=10, t=40, b=10), plot_bgcolor="rgba(0,0,0,0)",
                      paper_bgcolor="rgba(0,0,0,0)", legend_title_text="")
    fig.update_xaxes(gridcolor="rgba(128,128,128,0.2)", title=None)
    fig.update_yaxes(gridcolor="rgba(128,128,128,0.2)", title=None)
    return fig


conn, hub = get_hub(str(DB_PATH))
summary_path = DB_PATH.parent / "run_summary.json"
saved = json.loads(summary_path.read_text()) if summary_path.exists() else {}
s = M.summary(conn)

# ------------------------------------------------------------------ header
st.title("Connected Equipment Parts Integration Hub")
st.markdown(f"Created by **Alexa Gonzales Tuesta**  |  [GitHub repository]({GITHUB_URL})  |  [LinkedIn]({LINKEDIN_URL})")
st.markdown(
    """
**The problem.** When a machine breaks down on a job site, the fault shows up in the telematics system, but the
parts order usually waits until someone calls the dealer, a technician drives out, diagnoses it, and types an order
into the ERP. Online parts orders arrive through a different system. The ERP, the dealer inventory system, the
storefront and the telematics platform do not talk to each other, so machines sit idle while people re-key data.

**What I built.** An integration hub that connects those four systems. It turns a machine fault or a web order into a
validated ERP sales order in milliseconds, finds the parts at the nearest dealer with stock, uses an AI model when the
machine does not send a fault code, and makes every failure visible, traceable and recoverable.
"""
)
with st.expander("How to use this console", expanded=False):
    st.markdown(
        """
1. **Overview** shows how many messages came in, what happened to them, and where parts were sourced.
2. **Integration health** shows retries and the dead letter queue (messages the hub could not process and why).
3. **Trace a message** shows every processing step for one message. This is how IT support answers "where is my order?"
4. **AI diagnosis** shows how accurate the model is and the trade-off behind the confidence threshold.
5. **Send a test event** lets you report a machine fault yourself and watch the hub create the order.
6. **Requirements and testing** shows every requirement and the automated tests that prove it.

All data is synthetic. Systems, dealers, customers and part numbers are fictional.
"""
    )

k = st.columns(5)
k[0].metric("Messages processed", f"{s['messages_received']:,}")
k[1].metric("ERP orders created", f"{s['orders_created']:,}")
k[2].metric("Touchless fault orders", f"{s['telematics_touchless_rate']:.0%}",
            help="Share of actionable machine fault events turned into an ERP order with no person involved")
k[3].metric("Duplicates blocked", f"{s['duplicates_blocked']:,}")
k[4].metric("p95 processing time", f"{s['latency']['p95_ms']:.1f} ms",
            help=f"Target under {C.P95_LATENCY_TARGET_MS} ms (NFR-01)")

tabs = st.tabs(["Overview", "Integration health", "Trace a message", "AI diagnosis", "Send a test event",
                "Requirements and testing"])

# ------------------------------------------------------------------ overview
with tabs[0]:
    o = M.orders(conn)
    c1, c2 = st.columns(2)
    out = M.outcomes(conn)
    fig = px.bar(out, x="messages", y="outcome", color="channel", orientation="h", barmode="group",
                 color_discrete_map=CHANNEL_COLORS, title="What happened to each inbound message",
                 hover_data={"messages": ":,"})
    c1.plotly_chart(style(fig), use_container_width=True)
    fm = o.fulfillment.value_counts().rename_axis("fulfillment").reset_index(name="orders")
    fm["fulfillment"] = fm["fulfillment"].str.replace("_", " ").str.title()
    fig = px.bar(fm, x="orders", y="fulfillment", orientation="h", text="orders",
                 title="Where parts were sourced", color_discrete_sequence=[BLUE])
    c2.plotly_chart(style(fig), use_container_width=True)
    st.caption("Assigned dealer = the machine's own dealer had stock. Regional transfer = another dealer in the "
               "same region filled it. Backorder = no dealer in the region had stock.")

    o["week"] = pd.to_datetime(o["created_at"]).dt.to_period("W").dt.start_time
    wk = o.groupby(["week", "channel"]).size().reset_index(name="orders")
    fig = px.line(wk, x="week", y="orders", color="channel", color_discrete_map=CHANNEL_COLORS,
                  title="Orders created per week, by channel", markers=True)
    fig.update_traces(line_width=2, marker_size=8)
    st.plotly_chart(style(fig, 300), use_container_width=True)

    by_dealer = o.groupby("dealer_name").agg(orders=("order_id", "count"), value=("total_value", "sum"),
                                             backorders=("fulfillment", lambda x: (x == "BACKORDER").sum()))
    by_dealer["backorder_rate"] = (100 * by_dealer.backorders / by_dealer.orders).round(1)
    st.markdown("**Orders by dealer** (sort any column)")
    st.dataframe(by_dealer.sort_values("orders", ascending=False), use_container_width=True,
                 column_config={"value": st.column_config.NumberColumn("order value", format="$%,.0f"),
                                "backorder_rate": st.column_config.ProgressColumn("backorder rate", min_value=0,
                                                                                  max_value=100, format="%.0f%%")})

# ------------------------------------------------------------------ health
with tabs[1]:
    dl = M.dead_letters(conn)
    h = st.columns(4)
    h[0].metric("DMS retries performed", f"{s['dms_retries']:,}")
    h[1].metric("Dead letters (all time)", f"{len(dl):,}")
    h[2].metric("Recovered by replay", f"{dl.resolved_at.notna().sum():,}")
    h[3].metric("Held for a person to fix", f"{dl.resolved_at.isna().sum():,}",
                help="Bad input or missing master data. Retrying would not help, so these wait for the owning team.")
    dl["state"] = dl.resolved_at.notna().map({True: "Resolved by replay", False: "Open"})
    agg = dl.groupby(["reason_code", "state"]).size().reset_index(name="messages")
    fig = px.bar(agg, x="messages", y="reason_code", color="state", orientation="h", barmode="stack",
                 color_discrete_map={"Open": ORANGE, "Resolved by replay": BLUE}, title="Dead letters by reason")
    fig.update_traces(marker_line_color="rgba(0,0,0,0)")
    st.plotly_chart(style(fig), use_container_width=True)
    st.markdown(
        "| Reason | What it means | Who fixes it |\n|---|---|---|\n"
        "| DMS_UNAVAILABLE | Dealer system down after 3 retries | Automatic replay when it is back up |\n"
        "| MASTER_DATA_MISSING | Machine or customer not in ERP | Master data team adds it, then replay |\n"
        "| VALIDATION_FAILED | Payload broke the contract | Source system team (bug in sender) |\n"
        "| PRICE_MISMATCH | Storefront price is stale | Pricing team confirms price with customer |\n"
        "| UNKNOWN_PART | Discontinued part in a saved cart | Storefront shows a substitute part |")
    st.markdown("**Open dead letters**")
    st.dataframe(dl[dl.resolved_at.isna()][["message_id", "channel", "reason_code", "detail", "attempts",
                                            "replayable"]], use_container_width=True, hide_index=True)
    lat = M.all_attempts(conn)
    fig = px.histogram(lat, x="latency_ms", nbins=60, title="Processing time per message (ms)",
                       color_discrete_sequence=[BLUE])
    fig.add_vline(x=s["latency"]["p95_ms"], line_dash="dash", line_color=INK_2,
                  annotation_text=f"p95 {s['latency']['p95_ms']:.1f} ms")
    st.plotly_chart(style(fig, 280), use_container_width=True)

# ------------------------------------------------------------------ trace
with tabs[2]:
    st.markdown("Pick a message to see every step the hub took. Examples are pre-filled for interesting cases.")
    examples = {
        "Order with regional transfer": "SELECT source_message_id FROM erp_orders WHERE fulfillment='REGIONAL_TRANSFER' LIMIT 1",
        "Recovered after a dealer outage": "SELECT message_id FROM hub_dead_letter WHERE resolved_at IS NOT NULL LIMIT 1",
        "AI diagnosis, auto-ordered": "SELECT source_message_id FROM erp_orders WHERE ai_assisted=1 LIMIT 1",
        "AI unsure, sent to review": "SELECT message_id FROM hub_manual_review LIMIT 1",
        "Price mismatch held": "SELECT message_id FROM hub_dead_letter WHERE reason_code='PRICE_MISMATCH' LIMIT 1",
    }
    choice = st.selectbox("Example", list(examples))
    row = conn.execute(examples[choice]).fetchone()
    mid = st.text_input("Message id", value=row[0] if row else "")
    if mid:
        inbound = conn.execute("SELECT * FROM hub_inbound WHERE message_id=?", (mid,)).fetchone()
        if inbound is None:
            st.warning("No message with that id was received.")
        else:
            st.markdown(f"**{mid}**  |  channel {inbound['channel']}  |  final status **{inbound['status']}**")
            trail = pd.read_sql("SELECT logged_at, step, status, detail FROM hub_audit WHERE message_id=? ORDER BY id",
                                conn, params=(mid,))
            st.dataframe(trail, use_container_width=True, hide_index=True)
            with st.expander("Original payload"):
                st.json(json.loads(inbound["payload"]))

# ------------------------------------------------------------------ AI
with tabs[3]:
    off, live = saved.get("ai_offline_eval", {}), saved.get("ai_live_eval", {})
    a = st.columns(4)
    if off:
        a[0].metric("Top-1 accuracy (held-out)", f"{off['top1_accuracy']:.0%}")
        a[1].metric("Top-3 accuracy (held-out)", f"{off['top3_accuracy']:.0%}")
        a[2].metric("Precision of auto-orders", f"{off['auto_order_precision']:.0%}", help="NFR-05 target 85%")
        a[3].metric("Share auto-ordered", f"{off['auto_order_coverage']:.0%}")
    st.markdown(
        f"""When a machine sends no fault code (older machines) or a code the catalog does not know yet, the hub reads
the operator's note, for example *"boom drifts down when parked, oil on the ground"*, and predicts the fault with a
TF-IDF and logistic regression model trained on {C.N_HISTORY_NOTES:,} past service notes. If the model is at least
**{C.AI_CONFIDENCE_THRESHOLD:.0%}** confident it orders the parts kit; otherwise a service advisor reviews it."""
    )
    curve = pd.DataFrame(saved.get("ai_threshold_curve", []))
    if not curve.empty:
        long = curve.melt("threshold", var_name="measure", value_name="share")
        long["measure"] = long["measure"].map({"auto_order_coverage": "Share of notes auto-ordered",
                                               "auto_order_precision": "Auto-orders that were correct"})
        fig = px.line(long, x="threshold", y="share", color="measure", markers=True,
                      color_discrete_sequence=[BLUE, ORANGE],
                      title="Choosing the threshold: accuracy vs. manual workload")
        fig.update_traces(line_width=2, marker_size=8)
        fig.update_yaxes(tickformat=".0%", range=[0, 1.02])
        fig.add_vline(x=C.AI_CONFIDENCE_THRESHOLD, line_dash="dash", line_color=INK_2, annotation_text="chosen")
        st.plotly_chart(style(fig, 320), use_container_width=True)
        st.caption("A wrong auto-order ships the wrong parts to a job site, so I chose the point where about 87% of "
                   "auto-orders are correct and the rest go to a person.")
    if live:
        st.markdown(f"**On live traffic:** {live['ai_diagnoses']} events needed AI. {live['auto_ordered']} were "
                    f"auto-ordered ({live['accuracy_auto_ordered']:.0%} correct) and {live['sent_to_review']} went "
                    "to review.")
    st.markdown("**Manual review queue**")
    st.dataframe(pd.read_sql("SELECT message_id, machine_id, predicted_fault, confidence, symptom_note, status "
                             "FROM hub_manual_review ORDER BY confidence", conn),
                 use_container_width=True, hide_index=True)

# ------------------------------------------------------------------ try it
with tabs[4]:
    st.markdown("Report a machine fault and watch the hub process it. Leave the fault code empty to use the AI.")
    machines = [r[0] for r in conn.execute("SELECT machine_id FROM erp_machines ORDER BY machine_id LIMIT 200")]
    faults = [""] + [r[0] for r in conn.execute("SELECT fault_code FROM erp_fault_catalog ORDER BY fault_code")]
    with st.form("event"):
        f1, f2, f3 = st.columns(3)
        m_id = f1.selectbox("Machine", machines)
        code = f2.selectbox("Fault code", faults, format_func=lambda x: x or "None (let the AI diagnose)")
        sev = f3.selectbox("Severity", [3, 2, 1], format_func=lambda x: {3: "3 critical", 2: "2 warning",
                                                                           1: "1 info"}[x])
        note = st.text_input("Operator note", "engine overheating, coolant temp in red")
        sent = st.form_submit_button("Send event to the hub")
    if sent:
        n = conn.execute("SELECT COUNT(*) FROM hub_inbound WHERE message_id LIKE 'UI-%'").fetchone()[0]
        payload = {"event_id": f"UI-{n + 1:05d}", "machine_id": m_id, "occurred_at": pd.Timestamp.now().isoformat(),
                   "fault_code": code or None, "severity": sev, "symptom_note": note, "engine_hours": 5000}
        res = hub.process_telematics(payload)
        st.success(f"Outcome: **{res.outcome}**  {res.detail}  ({res.latency_ms:.1f} ms)")
        if res.order_id:
            st.dataframe(pd.read_sql("SELECT l.line_no, l.part_number, p.description, l.quantity, l.unit_price, "
                                     "l.fulfilling_dealer_id, l.line_status FROM erp_order_lines l JOIN erp_parts p "
                                     "USING (part_number) WHERE order_id=?", conn, params=(res.order_id,)),
                         use_container_width=True, hide_index=True)
        st.dataframe(pd.read_sql("SELECT step, status, detail FROM hub_audit WHERE message_id=? ORDER BY id",
                                 conn, params=(res.message_id,)), use_container_width=True, hide_index=True)

# ------------------------------------------------------------------ requirements
with tabs[5]:
    rtm_path = ROOT / "docs" / "requirements_traceability.csv"
    if rtm_path.exists():
        rtm = pd.read_csv(rtm_path)
        covered = (rtm["tests_passed"] > 0).sum()
        st.metric("Requirements verified by passing automated tests", f"{covered} of {len(rtm)}")
        st.dataframe(rtm, use_container_width=True, hide_index=True)
    else:
        st.info("Run `pytest` then `python tools/build_rtm.py` to generate the traceability matrix.")
    st.markdown("Full documents: business requirements, solution design, process flows, system test plan and "
                f"troubleshooting runbook are in the [docs folder]({GITHUB_URL}/tree/main/docs).")
