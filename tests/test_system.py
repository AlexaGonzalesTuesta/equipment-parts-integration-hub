"""System tests: the whole pipeline end to end, the REST API, and the dashboard."""
import json
import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from conftest import make_event
from hub.api import create_app
from hub.db import connect
from hub.pipeline import run

pytestmark = pytest.mark.system


@pytest.fixture(scope="module")
def full_run(tmp_path_factory):
    db = tmp_path_factory.mktemp("e2e") / "hub.db"
    summary = run(db, quiet=True)
    return db, summary


@pytest.mark.req("NFR-06")
def test_every_message_reaches_one_terminal_outcome(full_run):
    db, _ = full_run
    c = connect(db)
    received = c.execute("SELECT COUNT(*) FROM hub_inbound").fetchone()[0]
    with_result = c.execute("SELECT COUNT(DISTINCT message_id) FROM hub_results").fetchone()[0]
    stuck = c.execute("SELECT COUNT(*) FROM hub_inbound WHERE status = 'RECEIVED'").fetchone()[0]
    lines_in = sum(1 for _ in open(db.parent / "inbound" / "telematics_events.jsonl")) + \
        sum(1 for _ in open(db.parent / "inbound" / "web_orders.jsonl"))
    dup = c.execute("SELECT COUNT(*) FROM hub_results WHERE outcome='DUPLICATE'").fetchone()[0]
    assert received == with_result and stuck == 0
    assert received + dup == lines_in            # every line in the feeds is accounted for


@pytest.mark.req("FR-08", "FR-11")
def test_orders_match_outcomes_one_to_one(full_run):
    db, s = full_run
    c = connect(db)
    assert s["orders_created"] == s["outcomes"]["ORDER_CREATED"]
    dupes = c.execute("SELECT source_message_id FROM erp_orders GROUP BY 1 HAVING COUNT(*) > 1").fetchall()
    assert dupes == []


@pytest.mark.req("NFR-03")
def test_inventory_invariants_hold_after_full_run(full_run):
    db, _ = full_run
    c = connect(db)
    bad = c.execute("SELECT COUNT(*) FROM dms_inventory WHERE on_hand < 0 OR reserved < 0 OR reserved > on_hand")
    assert bad.fetchone()[0] == 0


@pytest.mark.req("FR-09", "FR-10")
def test_outage_messages_recovered_by_replay(full_run):
    db, s = full_run
    assert s["dms_retries"] > 0
    assert s["dead_letters_resolved_by_replay"] > 0
    c = connect(db)
    open_dms = c.execute("SELECT COUNT(*) FROM hub_dead_letter WHERE reason_code='DMS_UNAVAILABLE' "
                         "AND resolved_at IS NULL").fetchone()[0]
    assert open_dms == 0


@pytest.fixture
def client(full_run, tmp_path):
    db = tmp_path / "hub.db"
    shutil.copy(full_run[0], db)
    return TestClient(create_app(db))


@pytest.mark.req("FR-01", "FR-14")
def test_api_telematics_event_round_trip(client):
    ev = make_event("M00001", event_id="API-0001", fault="ELE-522", note="click but no crank",
                    when="2026-12-15T10:00:00")
    r = client.post("/v1/telematics/events", json=ev)
    assert r.status_code == 201, r.text
    order = client.get(f"/v1/orders/{r.json()['order_id']}").json()
    assert order["source_message_id"] == "API-0001" and len(order["lines"]) >= 1
    assert client.post("/v1/telematics/events", json=ev).json()["outcome"] == "DUPLICATE"
    trail = client.get("/v1/messages/API-0001").json()["audit_trail"]
    assert trail[0]["step"] == "VALIDATE"


@pytest.mark.req("FR-02", "FR-03")
def test_api_storefront_order_and_validation(client):
    c = client
    bad = c.post("/v1/storefront/orders", json={"web_order_id": "API-W1", "customer_id": "C0001",
                                                 "dealer_id": "D01", "submitted_at": "2026-10-01T00:00:00",
                                                 "lines": []})
    assert bad.status_code == 422 and bad.json()["outcome"] == "REJECTED"
    assert c.get("/health").json()["status"] == "ok"
    assert c.get("/v1/dead-letters").status_code == 200
    assert c.get("/v1/metrics").json()["orders_created"] > 0


@pytest.mark.req("FR-15")
def test_dashboard_renders_without_errors(full_run, monkeypatch):
    from streamlit.testing.v1 import AppTest
    monkeypatch.setenv("HUB_DB_PATH", str(full_run[0]))
    at = AppTest.from_file(str(Path(__file__).resolve().parent.parent / "app" / "console.py"), default_timeout=120).run()
    assert not at.exception, at.exception
    assert len(at.metric) >= 4
