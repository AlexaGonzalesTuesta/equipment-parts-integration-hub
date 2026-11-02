"""Unit tests: canonical schema validation and the AI recommender in isolation."""
import pytest
from pydantic import ValidationError

from hub import config as C
from hub.models import TelematicsEvent, WebOrder

pytestmark = pytest.mark.unit


@pytest.mark.req("FR-03")
def test_valid_event_parses():
    """A well-formed telematics event maps to the canonical model."""
    ev = TelematicsEvent(event_id="TE-1", machine_id="M00001", occurred_at="2026-08-01T10:00:00",
                         fault_code="  ", severity=2, symptom_note="x", engine_hours=10)
    assert ev.fault_code is None  # blank codes are normalized to missing


@pytest.mark.req("FR-03", "NFR-04")
@pytest.mark.parametrize("field,value", [("severity", 7), ("machine_id", "UNKNOWN"), ("engine_hours", -5),
                                         ("machine_id", "M00001' OR '1'='1")])
def test_malformed_event_is_rejected(field, value):
    """Out-of-range or badly formatted fields fail validation before any system is touched."""
    payload = {"event_id": "TE-1", "machine_id": "M00001", "occurred_at": "2026-08-01T10:00:00",
               "fault_code": "HYD-101", "severity": 2, "symptom_note": "", "engine_hours": 10, field: value}
    with pytest.raises(ValidationError):
        TelematicsEvent(**payload)


@pytest.mark.req("FR-03")
@pytest.mark.parametrize("lines", [[], [{"part_number": "PN-10001", "quantity": 0, "unit_price": 10}],
                                   [{"part_number": "ABC", "quantity": 1, "unit_price": 10}]])
def test_malformed_web_order_is_rejected(lines):
    """Storefront orders need at least one line, positive quantities and valid part numbers."""
    with pytest.raises(ValidationError):
        WebOrder(web_order_id="WO-1", customer_id="C0001", dealer_id="D01", submitted_at="2026-08-01T09:00:00",
                 lines=lines)


@pytest.mark.req("FR-06")
def test_recommender_diagnoses_clear_symptoms(landscape):
    """A clear description of a boom cylinder leak is diagnosed confidently."""
    pred = landscape["ai"].predict("oil dripping from boom cylinder, boom drifts down when parked")
    assert pred.fault_code == "HYD-122"
    assert pred.confidence >= C.AI_CONFIDENCE_THRESHOLD
    assert [c for c, _ in pred.top3][0] == "HYD-122"
    assert pred.top3 == sorted(pred.top3, key=lambda x: -x[1])


@pytest.mark.req("FR-06")
def test_recommender_is_unsure_about_vague_notes(landscape):
    """A vague note gets low confidence, which is what triggers human review."""
    assert landscape["ai"].predict("machine down").confidence < C.AI_CONFIDENCE_THRESHOLD


@pytest.mark.req("NFR-05")
def test_ai_auto_order_precision_meets_target(landscape):
    """On held-out notes, AI diagnoses above the threshold are at least 85% correct."""
    m = landscape["ai"].metrics
    assert m["auto_order_precision"] >= 0.85, m
    assert m["top3_accuracy"] >= 0.90, m
