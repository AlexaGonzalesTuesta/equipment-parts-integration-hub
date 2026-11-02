"""Shared fixtures and the requirement-traceability hook.

Tag a test with the requirement(s) it verifies:
    @pytest.mark.req("FR-07", "NFR-03")
After a run, reports/test_results.json maps every requirement to its tests and
results; tools/build_rtm.py turns that into the traceability matrix.
"""
import json
import shutil
from datetime import datetime
from pathlib import Path

import pytest

from hub import config as C
from hub.connectors import DMSConnector
from hub.db import connect
from hub.recommender import train_from_db
from hub.service import IntegrationHub
from hub.simulate import generate

REPORT = Path(__file__).resolve().parent.parent / "reports" / "test_results.json"
_results = {}


def pytest_configure(config):
    for name, desc in [("req(*ids)", "requirement ids verified by this test"),
                       ("unit", "unit test"), ("integration", "integration test across connectors"),
                       ("system", "end-to-end system test"), ("nonfunctional", "performance/security/reliability")]:
        config.addinivalue_line("markers", f"{name}: {desc}")


def pytest_collection_modifyitems(items):
    for item in items:
        reqs = [r for m in item.iter_markers("req") for r in m.args]
        level = next((m for m in ("unit", "integration", "system", "nonfunctional") if item.get_closest_marker(m)),
                     "unclassified")
        _results[item.nodeid] = {"requirements": reqs, "level": level, "outcome": "not run",
                                 "doc": (item.function.__doc__ or "").strip().split("\n")[0]}


def pytest_runtest_logreport(report):
    if report.nodeid in _results and (report.when == "call" or report.outcome != "passed"):
        _results[report.nodeid]["outcome"] = report.outcome


def pytest_sessionfinish(session):
    REPORT.parent.mkdir(exist_ok=True)
    REPORT.write_text(json.dumps({"run_at": datetime.now().isoformat(timespec="seconds"),
                                  "tests": _results}, indent=2))


# ---------------------------------------------------------------- fixtures
@pytest.fixture(scope="session")
def landscape(tmp_path_factory):
    """Generate the enterprise landscape once; each test gets its own copy."""
    base = tmp_path_factory.mktemp("landscape")
    db = base / "golden.db"
    generate(db, base / "inbound")
    conn = connect(db)
    ai = train_from_db(conn)
    conn.close()
    return {"db": db, "inbound": base / "inbound", "ai": ai}


@pytest.fixture
def conn(landscape, tmp_path):
    db = tmp_path / "hub.db"
    shutil.copy(landscape["db"], db)
    c = connect(db)
    yield c
    c.close()


@pytest.fixture
def reliable_dms(conn):
    return DMSConnector(conn, failure_rate=0.0, outages=[])


@pytest.fixture
def hub(conn, landscape, reliable_dms):
    return IntegrationHub(conn, landscape["ai"], dms=reliable_dms)


@pytest.fixture
def machine(conn):
    """A real machine from master data with its dealer and region."""
    return conn.execute("""SELECT m.*, d.region FROM erp_machines m JOIN erp_dealers d USING (dealer_id)
                           ORDER BY machine_id LIMIT 1""").fetchone()


def make_event(machine_id, event_id="TE-T00001", fault="HYD-122", severity=3, note="oil dripping from boom cylinder",
               when="2026-08-01T10:00:00"):
    return {"event_id": event_id, "machine_id": machine_id, "occurred_at": when, "fault_code": fault,
            "severity": severity, "symptom_note": note, "engine_hours": 4200.0}


def make_web_order(conn, order_id="WO-T00001", n_lines=2, qty=1):
    cust = conn.execute("SELECT customer_id FROM erp_customers ORDER BY customer_id LIMIT 1").fetchone()[0]
    dealer = conn.execute("SELECT dealer_id FROM erp_dealers ORDER BY dealer_id LIMIT 1").fetchone()[0]
    parts = conn.execute("SELECT part_number, list_price FROM erp_parts ORDER BY list_price LIMIT ?",
                         (n_lines,)).fetchall()
    return {"web_order_id": order_id, "customer_id": cust, "dealer_id": dealer, "submitted_at": "2026-08-01T09:00:00",
            "lines": [{"part_number": p[0], "quantity": qty, "unit_price": p[1]} for p in parts]}


def set_stock(conn, dealer_id, part_number, qty):
    conn.execute("UPDATE dms_inventory SET on_hand = ?, reserved = 0 WHERE dealer_id = ? AND part_number = ?",
                 (qty, dealer_id, part_number))
    conn.commit()
