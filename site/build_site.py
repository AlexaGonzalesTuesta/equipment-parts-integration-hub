"""Build the static operations console published on GitHub Pages.

Runs every demo scenario through the real integration hub (app/demo.py, the same code the
Streamlit console uses) on a copy of the 90-day database, captures what the hub produced,
and writes _site/index.html. Nothing on the page is typed in by hand.

Run:  python run_pipeline.py  then  python site/build_site.py
"""
import json
import shutil
import sqlite3
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from hub import config as C  # noqa: E402
from hub import metrics as M  # noqa: E402
from hub.db import connect  # noqa: E402
from hub.recommender import PartsRecommender  # noqa: E402
from app.demo import (OUTCOME_CHIP, REASONS, SCENARIOS, reliable_hub, render_steps,  # noqa: E402
                      result_parts, run_scenario)

SITE = ROOT / "site"
OUT = ROOT / "_site"
BASE_DB = C.DB_PATH


def model_from(conn):
    rows = conn.execute("SELECT note_text, fault_code FROM ml_history_notes").fetchall()
    return PartsRecommender().fit([r["note_text"] for r in rows], [r["fault_code"] for r in rows], evaluate=False)


def record_scenarios(conn, model):
    """Run each scenario once through the hub and keep the rendered result."""
    state = {"sent": 0, "sandbox_id": "DEMO", "used_machines": []}
    results, trace_ids = {}, []
    for key, label, desc in SCENARIOS:
        last = run_scenario(key, conn, model, state)
        parts = result_parts(conn, last)
        if parts is None:
            raise SystemExit(f"scenario {key} produced no result")
        entry = {"label": label, "desc": desc, "first": parts}
        if parts["replayable"]:                       # dealer outage: replay once the system is back
            reliable_hub(conn, model).replay_one(last["message_id"])
            entry["replayed"] = result_parts(conn, last)
        results[key] = entry
        trace_ids.append(last["message_id"])
        print(f"  {key:10s} {parts['outcome']:14s} {parts['kicker']}")
    return results, trace_ids


def exceptions(conn):
    dl = pd.read_sql("SELECT * FROM hub_dead_letter WHERE resolved_at IS NULL ORDER BY id DESC", conn)
    review = pd.read_sql("SELECT * FROM hub_manual_review WHERE status = 'OPEN' ORDER BY id DESC", conn)
    descs = dict(conn.execute("SELECT fault_code, description FROM erp_fault_catalog").fetchall())
    held = [{"message_id": r.message_id, "channel": r.channel.title(), "problem": REASONS.get(r.reason_code, (r.reason_code, "", ""))[0],
             "owner": REASONS.get(r.reason_code, ("", "IT support", ""))[1],
             "next_step": REASONS.get(r.reason_code, ("", "", "Investigate"))[2], "detail": r.detail}
            for r in dl.itertuples()]
    advisor = [{"message_id": r.message_id, "machine_id": r.machine_id, "note": r.symptom_note,
                "guess": f"{r.predicted_fault}  {descs.get(r.predicted_fault, '')}", "confidence": round(r.confidence, 2)}
               for r in review.itertuples()]
    return held, advisor


def ninety_days(base, s):
    o = M.orders(base)
    o["week"] = pd.to_datetime(o["created_at"]).dt.to_period("W").dt.start_time
    first, last = pd.to_datetime(o["created_at"]).min().normalize(), pd.to_datetime(o["created_at"]).max().normalize()
    full = o[(o["week"] >= first) & (o["week"] <= last - pd.Timedelta(days=6))]
    wk = full.groupby(["week", "channel"]).size().unstack(fill_value=0)
    weekly = {"weeks": [d.strftime("%Y-%m-%d") for d in wk.index],
              "telematics": wk.get("TELEMATICS", pd.Series(0, wk.index)).astype(int).tolist(),
              "ecommerce": wk.get("ECOMMERCE", pd.Series(0, wk.index)).astype(int).tolist()}
    dealers = (o.groupby("dealer_name").agg(orders=("order_id", "count"),
                                           own=("fulfillment", lambda x: (x == "ASSIGNED_DEALER").mean()),
                                           transfer=("fulfillment", lambda x: (x == "REGIONAL_TRANSFER").mean()),
                                           backorder=("fulfillment", lambda x: (x == "BACKORDER").mean()))
               .reset_index().sort_values("backorder", ascending=False))
    dl = M.dead_letters(base)
    dl["problem"] = dl.reason_code.map(lambda c: REASONS.get(c, (c, "", ""))[0])
    dl["owner"] = dl.reason_code.map(lambda c: REASONS.get(c, ("", "IT support", ""))[1])
    ex = (dl.assign(recovered=dl.resolved_at.notna()).groupby(["problem", "owner"])
          .agg(messages=("message_id", "count"), recovered=("recovered", "sum")).reset_index()
          .sort_values("messages", ascending=False))
    retries = int(base.execute("SELECT COUNT(*) FROM hub_audit WHERE status = 'RETRY'").fetchone()[0])
    return {"weekly": weekly, "dealers": dealers.round(4).to_dict(orient="records"),
            "exceptions": ex.astype({"recovered": int}).to_dict(orient="records"), "retries": retries,
            "curve": s.get("ai_threshold_curve", []), "ai": s.get("ai_offline_eval", {})}


def kpis(base, s):
    o = M.orders(base)
    dl = M.dead_letters(base)
    mix = s["fulfillment_mix"]
    dms = dl[dl.reason_code == "DMS_UNAVAILABLE"]
    return {"touchless": s["telematics_touchless_rate"],
            "filled": (mix.get("ASSIGNED_DEALER", 0) + mix.get("REGIONAL_TRANSFER", 0)) / max(sum(mix.values()), 1),
            "resends": s["duplicates_blocked"], "dup_orders": int(o.source_message_id.duplicated().sum()),
            "dms_fixed": int(dms.resolved_at.notna().sum()), "dms_total": int(len(dms)),
            "messages": s["messages_received"], "orders": s["orders_created"], "value": s["order_value_total"],
            "p95": s["latency"]["p95_ms"]}


def main():
    if not BASE_DB.exists():
        raise SystemExit("Run python run_pipeline.py first")
    s = json.loads((BASE_DB.parent / "run_summary.json").read_text())
    base = connect(BASE_DB)

    tmp = Path(tempfile.mkdtemp()) / "sandbox.db"
    src, dst = sqlite3.connect(str(BASE_DB)), sqlite3.connect(str(tmp))
    src.backup(dst)
    src.close()
    dst.close()
    conn = connect(tmp)
    model = model_from(conn)
    print("Recording scenarios through the hub:")
    scenarios, trace_ids = record_scenarios(conn, model)
    held, advisor = exceptions(conn)
    ids = trace_ids + [h["message_id"] for h in held] + [a["message_id"] for a in advisor]
    traces = {}
    for mid in dict.fromkeys(ids):
        status = conn.execute("SELECT status FROM hub_inbound WHERE message_id = ?", (mid,)).fetchone()
        traces[mid] = {"status": OUTCOME_CHIP.get(status["status"], (status["status"], "grey")) if status else None,
                       "steps": render_steps(conn, mid)}

    rtm = pd.read_csv(ROOT / "docs" / "requirements_traceability.csv")
    data = {
        "built": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "kpi": kpis(base, s), "scenarios": scenarios, "order": [k for k, _, _ in SCENARIOS],
        "held": held, "advisor": advisor, "traces": traces, "results": ninety_days(base, s),
        "rtm": rtm[["req_id", "priority", "requirement", "test_levels", "tests_passed", "status"]].to_dict(orient="records"),
        "threshold": C.AI_CONFIDENCE_THRESHOLD, "p95_target": C.P95_LATENCY_TARGET_MS,
    }
    html = (SITE / "template.html").read_text(encoding="utf-8").replace(
        "/*__DATA__*/null", json.dumps(data, separators=(",", ":")).replace("</", "<\\/"))
    if "__DATA__" in html:
        raise SystemExit("template placeholder was not replaced")
    if OUT.exists():
        shutil.rmtree(OUT)
    OUT.mkdir()
    (OUT / "index.html").write_text(html, encoding="utf-8")
    shutil.copy(SITE / "vendor" / "plotly-basic.min.js", OUT / "plotly-basic.min.js")
    (OUT / ".nojekyll").write_text("")
    conn.close()
    base.close()
    print(f"wrote {OUT / 'index.html'} ({len(html) / 1e6:.2f} MB)")


if __name__ == "__main__":
    main()
