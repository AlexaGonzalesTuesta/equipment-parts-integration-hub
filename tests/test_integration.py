"""Integration tests: the hub working with the ERP and DMS connectors."""
import pytest

from conftest import make_event, make_web_order, set_stock
from hub import config as C
from hub.connectors import DMSConnector, DMSUnavailable
from hub.service import IntegrationHub

pytestmark = pytest.mark.integration


class FailFirstN(DMSConnector):
    """DMS that times out on the first n calls, then works."""
    def __init__(self, conn, n):
        super().__init__(conn, failure_rate=0.0, outages=[])
        self.remaining = n

    def _check_up(self, dealer_id, as_of):
        self.calls += 1
        if self.remaining > 0:
            self.remaining -= 1
            raise DMSUnavailable(f"{dealer_id} DMS timeout")


def order_lines(conn, order_id):
    return conn.execute("SELECT * FROM erp_order_lines WHERE order_id = ? ORDER BY line_no", (order_id,)).fetchall()


def stock_everywhere(conn, part_number, qty):
    conn.execute("UPDATE dms_inventory SET on_hand = ?, reserved = 0 WHERE part_number = ?", (qty, part_number))
    conn.commit()


@pytest.mark.req("FR-05", "FR-08")
def test_known_fault_creates_order_with_catalog_kit(hub, conn, machine):
    """A known fault code creates one ERP order whose lines equal the catalog parts kit."""
    for pn, in conn.execute("SELECT part_number FROM erp_fault_parts WHERE fault_code='HYD-122'"):
        stock_everywhere(conn, pn, 50)
    res = hub.process_telematics(make_event(machine["machine_id"]))
    assert res.outcome == "ORDER_CREATED" and res.order_status == "RESERVED"
    kit = conn.execute("SELECT part_number, quantity FROM erp_fault_parts WHERE fault_code='HYD-122' "
                       "ORDER BY part_number").fetchall()
    lines = order_lines(conn, res.order_id)
    assert [(l["part_number"], l["quantity"]) for l in lines] == [tuple(k) for k in kit]
    header = conn.execute("SELECT * FROM erp_orders WHERE order_id=?", (res.order_id,)).fetchone()
    assert header["customer_id"] == machine["customer_id"] and header["channel"] == "TELEMATICS"
    assert header["total_value"] > 0


@pytest.mark.req("FR-04")
def test_informational_fault_does_not_create_order(hub, conn, machine):
    """Severity 1 faults are logged only."""
    res = hub.process_telematics(make_event(machine["machine_id"], severity=1))
    assert res.outcome == "NO_ACTION" and res.order_id is None
    assert conn.execute("SELECT COUNT(*) FROM erp_orders").fetchone()[0] == 0


@pytest.mark.req("FR-06")
def test_ai_diagnoses_event_without_fault_code(hub, conn, machine):
    """No fault code + clear note = AI-assisted order for the predicted fault."""
    res = hub.process_telematics(make_event(machine["machine_id"], fault=None,
                                            note="oil dripping from boom cylinder, boom drifts down when parked"))
    assert res.outcome == "ORDER_CREATED"
    row = conn.execute("SELECT fault_code, ai_assisted FROM erp_orders WHERE order_id=?", (res.order_id,)).fetchone()
    assert tuple(row) == ("HYD-122", 1)


@pytest.mark.req("FR-06")
def test_low_confidence_goes_to_manual_review(hub, conn, machine):
    """Vague notes are not auto-ordered; they land in the service advisor queue with the top 3 guesses."""
    res = hub.process_telematics(make_event(machine["machine_id"], fault="HYD-999", note="machine down"))
    assert res.outcome == "MANUAL_REVIEW"
    review = conn.execute("SELECT * FROM hub_manual_review WHERE message_id='TE-T00001'").fetchone()
    assert review["status"] == "OPEN" and review["top3"].count("-") >= 3
    assert conn.execute("SELECT COUNT(*) FROM erp_orders").fetchone()[0] == 0


@pytest.mark.req("FR-07")
def test_sources_from_assigned_dealer_first(hub, conn, machine):
    for pn, in conn.execute("SELECT part_number FROM erp_fault_parts WHERE fault_code='HYD-122'"):
        stock_everywhere(conn, pn, 50)
    res = hub.process_telematics(make_event(machine["machine_id"]))
    assert {l["fulfilling_dealer_id"] for l in order_lines(conn, res.order_id)} == {machine["dealer_id"]}


@pytest.mark.req("FR-07")
def test_regional_transfer_when_assigned_dealer_is_out(hub, conn, machine):
    """If the assigned dealer has no stock, another dealer in the same region fills the line."""
    kit = [r[0] for r in conn.execute("SELECT part_number FROM erp_fault_parts WHERE fault_code='HYD-122'")]
    for pn in kit:
        stock_everywhere(conn, pn, 50)
        set_stock(conn, machine["dealer_id"], pn, 0)
    res = hub.process_telematics(make_event(machine["machine_id"]))
    lines = order_lines(conn, res.order_id)
    regions = {conn.execute("SELECT region FROM erp_dealers WHERE dealer_id=?", (l["fulfilling_dealer_id"],))
               .fetchone()[0] for l in lines}
    assert all(l["line_status"] == "TRANSFER" for l in lines)
    assert regions == {machine["region"]}
    assert machine["dealer_id"] not in {l["fulfilling_dealer_id"] for l in lines}


@pytest.mark.req("FR-07")
def test_backorder_when_region_is_out(hub, conn, machine):
    kit = [r[0] for r in conn.execute("SELECT part_number FROM erp_fault_parts WHERE fault_code='HYD-122'")]
    for pn in kit:
        stock_everywhere(conn, pn, 0)
    res = hub.process_telematics(make_event(machine["machine_id"]))
    assert res.order_status == "BACKORDERED"
    assert all(l["fulfilling_dealer_id"] is None for l in order_lines(conn, res.order_id))


@pytest.mark.req("FR-09")
def test_retry_recovers_from_transient_failures(conn, landscape, machine):
    """Two timeouts, then success: the order is still created and the retries are audited."""
    hub = IntegrationHub(conn, landscape["ai"], dms=FailFirstN(conn, 2))
    res = hub.process_telematics(make_event(machine["machine_id"]))
    assert res.outcome == "ORDER_CREATED"
    assert conn.execute("SELECT COUNT(*) FROM hub_audit WHERE status='RETRY'").fetchone()[0] == 2


@pytest.mark.req("FR-09", "FR-10")
def test_dead_letter_after_retries_then_replay(conn, landscape, machine):
    """A DMS that stays down exhausts 3 retries, the message is dead-lettered, and replay recovers it."""
    dms = FailFirstN(conn, C.MAX_RETRIES)
    hub = IntegrationHub(conn, landscape["ai"], dms=dms)
    res = hub.process_telematics(make_event(machine["machine_id"]))
    assert res.outcome == "DEAD_LETTER" and "DMS_UNAVAILABLE" in res.detail
    assert dms.calls == C.MAX_RETRIES
    dl = conn.execute("SELECT * FROM hub_dead_letter").fetchone()
    assert dl["replayable"] == 1 and dl["resolved_at"] is None

    replayed = hub.replay_dead_letters()
    assert replayed[0].outcome == "ORDER_CREATED"
    assert conn.execute("SELECT resolved_at FROM hub_dead_letter").fetchone()[0] is not None


@pytest.mark.req("FR-10")
def test_unknown_machine_is_replayable_dead_letter(hub, conn):
    res = hub.process_telematics(make_event("M99999"))
    assert res.outcome == "DEAD_LETTER" and "MASTER_DATA_MISSING" in res.detail
    assert conn.execute("SELECT replayable FROM hub_dead_letter").fetchone()[0] == 1


@pytest.mark.req("FR-11")
def test_duplicate_message_creates_one_order(hub, conn, machine):
    first = hub.process_telematics(make_event(machine["machine_id"]))
    second = hub.process_telematics(make_event(machine["machine_id"]))
    assert first.outcome == "ORDER_CREATED" and second.outcome == "DUPLICATE"
    assert conn.execute("SELECT COUNT(*) FROM erp_orders").fetchone()[0] == 1


@pytest.mark.req("FR-02", "FR-08", "FR-11")
def test_web_order_creates_order_and_blocks_double_submit(hub, conn):
    order = make_web_order(conn)
    for ln in order["lines"]:
        stock_everywhere(conn, ln["part_number"], 50)
    assert hub.process_web_order(order).outcome == "ORDER_CREATED"
    assert hub.process_web_order(order).outcome == "DUPLICATE"
    assert conn.execute("SELECT COUNT(*) FROM erp_orders WHERE channel='ECOMMERCE'").fetchone()[0] == 1


@pytest.mark.req("FR-12")
def test_unknown_part_is_rejected(hub, conn):
    order = make_web_order(conn)
    order["lines"][0]["part_number"] = "PN-99999"
    res = hub.process_web_order(order)
    assert res.outcome == "REJECTED" and "UNKNOWN_PART" in res.detail


@pytest.mark.req("FR-12")
def test_price_mismatch_is_held(hub, conn):
    order = make_web_order(conn)
    order["lines"][0]["unit_price"] = round(order["lines"][0]["unit_price"] * 0.8, 2)
    res = hub.process_web_order(order)
    assert res.outcome == "DEAD_LETTER" and "PRICE_MISMATCH" in res.detail
    assert conn.execute("SELECT replayable FROM hub_dead_letter").fetchone()[0] == 0


@pytest.mark.req("FR-12")
def test_price_within_tolerance_is_accepted(hub, conn):
    order = make_web_order(conn, n_lines=1)
    order["lines"][0]["unit_price"] = round(order["lines"][0]["unit_price"] * 1.005, 2)
    assert hub.process_web_order(order).outcome == "ORDER_CREATED"


@pytest.mark.req("FR-13")
def test_open_order_for_same_fault_is_not_duplicated(hub, conn, machine):
    """A machine that re-reports the same fault two days later does not get a second kit."""
    first = hub.process_telematics(make_event(machine["machine_id"], event_id="TE-A"))
    again = hub.process_telematics(make_event(machine["machine_id"], event_id="TE-B", when="2026-08-03T10:00:00"))
    later = hub.process_telematics(make_event(machine["machine_id"], event_id="TE-C", when="2026-08-20T10:00:00"))
    assert first.outcome == "ORDER_CREATED"
    assert again.outcome == "NO_ACTION" and again.order_id == first.order_id
    assert later.outcome == "ORDER_CREATED"


@pytest.mark.req("FR-14")
def test_audit_trail_records_each_step(hub, conn, machine):
    res = hub.process_telematics(make_event(machine["machine_id"]))
    steps = [r[0] for r in conn.execute("SELECT step FROM hub_audit WHERE message_id=? ORDER BY id",
                                        (res.message_id,))]
    for expected in ["VALIDATE", "ENRICH", "DIAGNOSE", "SOURCE", "ERP_ORDER"]:
        assert expected in steps
    assert steps.index("VALIDATE") < steps.index("SOURCE") < steps.index("ERP_ORDER")


@pytest.mark.req("NFR-07", "FR-06")
def test_confidence_threshold_is_configurable(hub, conn, machine, monkeypatch):
    """Changing the threshold in config changes routing without touching processing code."""
    monkeypatch.setattr(C, "AI_CONFIDENCE_THRESHOLD", 0.0)
    res = hub.process_telematics(make_event(machine["machine_id"], fault=None, note="machine down"))
    assert res.outcome == "ORDER_CREATED"
