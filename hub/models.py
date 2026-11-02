"""Canonical data model. Every source system is mapped into these contracts at the
edge of the hub, so downstream logic never depends on one system's field names."""
from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, Field, field_validator


class TelematicsEvent(BaseModel):
    """A fault reported by a connected machine (source: telematics platform)."""
    event_id: str = Field(min_length=3)
    machine_id: str = Field(pattern=r"^M\d{5}$")
    occurred_at: datetime
    fault_code: Optional[str] = None          # may be missing on older machines
    severity: int = Field(ge=1, le=3)          # 1 info, 2 warning, 3 critical
    symptom_note: str = ""                     # free text from operator or onboard diagnostics
    engine_hours: float = Field(ge=0)

    @field_validator("fault_code")
    @classmethod
    def blank_to_none(cls, v):
        return v.strip() or None if isinstance(v, str) else v


class OrderLineIn(BaseModel):
    part_number: str = Field(pattern=r"^PN-\d{5}$")
    quantity: int = Field(gt=0, le=500)
    unit_price: float = Field(gt=0)


class WebOrder(BaseModel):
    """An order placed on the customer e-commerce storefront."""
    web_order_id: str = Field(min_length=3)
    customer_id: str = Field(pattern=r"^C\d{4}$")
    dealer_id: str = Field(pattern=r"^D\d{2}$")
    submitted_at: datetime
    lines: List[OrderLineIn] = Field(min_length=1)


class ProcessingResult(BaseModel):
    """What the hub returns to the caller for every message it receives."""
    message_id: str
    channel: str
    outcome: str            # ORDER_CREATED, DUPLICATE, NO_ACTION, MANUAL_REVIEW, DEAD_LETTER, REJECTED
    order_id: Optional[str] = None
    order_status: Optional[str] = None
    detail: str = ""
    latency_ms: float = 0.0
