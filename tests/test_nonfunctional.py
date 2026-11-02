"""Non-functional tests: performance, reliability (rollback), data integrity and security."""
import json

import numpy as np
import pytest

from conftest import make_event
from hub import config as C
from hub.connectors import DMSConnector, DMSUnavailable
from hub.service import IntegrationHub

pytestmark = pytest.mark.nonfunctional


class FailOnSecondReserve(DMSConnector):
    """Availability checks work, the first reservation works, every later reservation call fails."""
    def __init__(self, conn):
        super().__init__(conn, failure_rate=0.0, outages=[])
        self.reserves = 0

    def reserve(self, dealer_id, part_number, qty, as_of):
        self.reserves += 1
        if self.reserves > 1:
            raise DMSUnavailable("DMS connection dropped mid-order")
        return super().reserve(dealer_id, part_number, qty, as_of)


@pytest.mark.req("NFR-02")
def test_failure_mid_order_rolls_back_all_reservations(conn, landscape, machine):
    """If line 2 cannot be reserved, line 1's reservation is undone and no order exists."""
    conn.execute("UPDATE dms_inventory SET on_hand = 50, reserved = 0")
    conn.commit()
    before = conn.execute("SELECT SUM(reserved) FROM dms_inventory").fetchone()[0]
    hub = IntegrationHub(conn, landscape["ai"], dms=FailOnSecondReserve(conn))
    res = hub.process_telematics(make_event(machine["machine_id"]))   # HYD-122 kit has 2 lines
    assert res.outcome == "DEAD_LETTER"
    assert conn.execute("SELECT SUM(reserved) FROM dms_inventory").fetchone()[0] == before
    assert conn.execute("SELECT COUNT(*) FROM erp_orders").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM erp_order_lines").fetchone()[0] == 0


@pytest.mark.req("NFR-04")
@pytest.mark.parametrize("bad_id", ["M00001'; DROP TABLE erp_orders;--", "M00001 OR 1=1", "<script>x</script>"])
def test_injection_attempts_are_rejected_without_side_effects(hub, conn, bad_id):
    res = hub.process_telematics(make_event(bad_id))
    assert res.outcome == "REJECTED" and "VALIDATION_FAILED" in res.detail
    assert conn.execute("SELECT name FROM sqlite_master WHERE name='erp_orders'").fetchone() is not None
    assert conn.execute("SELECT COUNT(*) FROM erp_orders").fetchone()[0] == 0


@pytest.mark.req("NFR-04")
def test_no_string_formatted_sql_in_source():
    """Static check: SQL is parameterized (no f-strings or % formatting inside execute calls)."""
    import re
    from pathlib import Path
    src = "".join(p.read_text() for p in (Path(__file__).parent.parent / "hub").glob("*.py"))
    assert not re.search(r"execute(many)?\(\s*f[\"']", src)
    assert not re.search(r"execute(many)?\([^)]*[\"']\s*%\s*\(", src)


@pytest.mark.req("NFR-01")
def test_p95_latency_under_target(conn, landscape):
    """1,000 real events from the inbound feed, p95 processing time under the target."""
    dms = DMSConnector(conn)  # realistic: includes random timeouts and retries
    hub = IntegrationHub(conn, landscape["ai"], dms=dms)
    with open(landscape["inbound"] / "telematics_events.jsonl") as f:
        events = [json.loads(line) for _, line in zip(range(1000), f)]
    latencies = [hub.process_telematics(e).latency_ms for e in events]
    p95 = float(np.percentile(latencies, 95))
    print(f"p95 latency {p95:.2f} ms over {len(latencies)} messages")
    assert p95 < C.P95_LATENCY_TARGET_MS
