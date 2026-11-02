"""KPIs and operational metrics computed from the hub's own tables.
Used by the pipeline summary, the dashboard, and the non-functional tests."""
import json

import pandas as pd

from . import config as C


def results(conn):
    """Final outcome per message. A replayed message counts once, by its last outcome;
    duplicate resends are counted separately and never replace the original outcome."""
    df = pd.read_sql("SELECT rowid AS rid, * FROM hub_results WHERE outcome != 'DUPLICATE'", conn)
    return df.sort_values("rid").groupby("message_id", as_index=False).last()


def all_attempts(conn):
    return pd.read_sql("SELECT * FROM hub_results", conn)


def latency_stats(conn):
    lat = all_attempts(conn)["latency_ms"]
    return {"p50_ms": round(lat.quantile(0.50), 2), "p95_ms": round(lat.quantile(0.95), 2),
            "p99_ms": round(lat.quantile(0.99), 2), "max_ms": round(lat.max(), 2), "messages": int(len(lat))}


def outcomes(conn):
    """Final outcome per message plus duplicate resends, by channel."""
    final = results(conn)[["channel", "outcome"]]
    dups = all_attempts(conn).query("outcome == 'DUPLICATE'")[["channel", "outcome"]]
    return pd.concat([final, dups]).groupby(["channel", "outcome"]).size().reset_index(name="messages")


def dead_letters(conn):
    return pd.read_sql("SELECT * FROM hub_dead_letter", conn)


def orders(conn):
    return pd.read_sql("SELECT o.*, d.name AS dealer_name, d.region FROM erp_orders o "
                       "JOIN erp_dealers d USING (dealer_id)", conn)


def retries(conn):
    return int(conn.execute("SELECT COUNT(*) FROM hub_audit WHERE status = 'RETRY'").fetchone()[0])


def ai_live_accuracy(conn, labels_path):
    """Compare AI diagnoses on live events with the true fault (known only because the data is synthetic)."""
    labels = pd.read_json(labels_path, lines=True)
    ai = pd.read_sql("""SELECT message_id AS event_id, detail FROM hub_audit
                        WHERE step = 'DIAGNOSE' AND status = 'AI'""", conn)
    ai["predicted"] = ai["detail"].str.extract(r"predicted (\S+)")[0]
    ai = ai.drop_duplicates("event_id", keep="last")
    m = ai.merge(labels, on="event_id")
    reviewed = pd.read_sql("SELECT message_id FROM hub_manual_review", conn)["message_id"]
    auto = m[~m["event_id"].isin(reviewed)]
    return {"ai_diagnoses": int(len(m)),
            "auto_ordered": int(len(auto)),
            "sent_to_review": int(m["event_id"].isin(reviewed).sum()),
            "accuracy_all": round(float((m["predicted"] == m["true_fault"]).mean()), 3) if len(m) else None,
            "accuracy_auto_ordered": round(float((auto["predicted"] == auto["true_fault"]).mean()), 3)
            if len(auto) else None}


def summary(conn, labels_path=None, ai_offline=None):
    res = results(conn)
    o = orders(conn)
    dl = dead_letters(conn)
    tel = res[res.channel == "TELEMATICS"]
    actionable = tel[tel.outcome != "REJECTED"]
    actionable = actionable[~actionable.detail.fillna("").str.startswith("Informational")]
    touchless = int((actionable.outcome == "ORDER_CREATED").sum())
    s = {
        "messages_received": int(len(res)),
        "orders_created": int(len(o)),
        "orders_by_channel": o.channel.value_counts().to_dict(),
        "order_value_total": round(float(o.total_value.sum()), 2),
        "fulfillment_mix": o.fulfillment.value_counts().to_dict(),
        "outcomes": res.outcome.value_counts().to_dict(),
        "duplicates_blocked": int((all_attempts(conn).outcome == "DUPLICATE").sum()),
        "dead_letters_total": int(len(dl)),
        "dead_letters_resolved_by_replay": int(dl.resolved_at.notna().sum()),
        "dead_letters_open": dl[dl.resolved_at.isna()].reason_code.value_counts().to_dict(),
        "dms_retries": retries(conn),
        "latency": latency_stats(conn),
        "telematics_touchless_rate": round(touchless / max(len(actionable), 1), 3),
        "telematics_touchless_orders": touchless,
        "est_hours_earlier_parts_reserved": touchless * C.BASELINE_FAULT_TO_RESERVATION_HOURS,
    }
    if ai_offline:
        s["ai_offline_eval"] = ai_offline
    if labels_path:
        s["ai_live_eval"] = ai_live_accuracy(conn, labels_path)
    return s


def write_summary(summary_dict, path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(summary_dict, indent=2, default=str))
