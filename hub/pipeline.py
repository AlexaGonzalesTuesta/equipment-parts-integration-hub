"""End-to-end run: build the landscape, train the AI model, push both inbound
feeds through the hub in time order, replay dead letters, write a KPI summary."""
import json
from datetime import datetime, timedelta

from . import config as C
from .db import connect
from .metrics import summary, write_summary
from .recommender import train_from_db
from .service import IntegrationHub
from .simulate import generate


def load_feed(path, ts_field, channel):
    with open(path) as f:
        rows = [json.loads(line) for line in f]
    return [(r.get(ts_field, ""), channel, r) for r in rows]


def run(db_path=C.DB_PATH, quiet=False):
    say = (lambda *a: None) if quiet else print
    data_dir = db_path.parent
    inbound = data_dir / "inbound"

    say("1/5 Generating enterprise landscape and inbound feeds ...")
    counts = generate(db_path, inbound)
    say("    ", counts)

    conn = connect(db_path)
    say("2/5 Training AI parts recommender ...")
    ai = train_from_db(conn)
    say("    offline evaluation:", ai.metrics)

    say("3/5 Processing inbound messages through the integration hub ...")
    hub = IntegrationHub(conn, ai)
    feed = (load_feed(inbound / "telematics_events.jsonl", "occurred_at", "TELEMATICS")
            + load_feed(inbound / "web_orders.jsonl", "submitted_at", "ECOMMERCE"))
    feed.sort(key=lambda x: x[0])
    current_day = None
    for ts, channel, payload in feed:
        day = str(ts)[:10]
        if current_day and day != current_day:
            hub.dms.end_of_day()
        current_day = day
        if channel == "TELEMATICS":
            hub.process_telematics(payload)
        else:
            hub.process_web_order(payload)

    say("4/5 Replaying dead letters after outages are resolved ...")
    after = datetime.fromisoformat(C.SIM_START) + timedelta(days=C.SIM_DAYS + 1)
    replayed = hub.replay_dead_letters(as_of=after)
    say(f"    replayed {len(replayed)}, recovered {sum(r.outcome != 'DEAD_LETTER' for r in replayed)}")

    say("5/5 Writing KPI summary ...")
    s = summary(conn, inbound / "telematics_labels.jsonl", ai.metrics)
    s["ai_threshold_curve"] = ai.curve
    write_summary(s, data_dir / "run_summary.json")
    conn.close()
    say(json.dumps({k: s[k] for k in ["messages_received", "orders_created", "telematics_touchless_rate",
                                      "dead_letters_total", "dead_letters_resolved_by_replay", "latency"]},
                   indent=2))
    return s
