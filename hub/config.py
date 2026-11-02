"""Central configuration. Every business rule threshold lives here so it can be
reviewed with stakeholders and changed without touching processing logic."""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
DB_PATH = DATA_DIR / "hub.db"

SEED = 42

# Synthetic data volumes
N_DEALERS = 12
N_CUSTOMERS = 300
N_MACHINES = 1500
N_HISTORY_NOTES = 4000      # labeled historical service notes used to train the AI model
N_TELEMATICS_EVENTS = 3000  # live events pushed through the hub
N_WEB_ORDERS = 800          # e-commerce storefront orders pushed through the hub
SIM_START = "2026-07-01"
SIM_DAYS = 90

# Business rules (see docs/01_business_requirements.md)
MIN_SEVERITY_FOR_ORDER = 2          # FR-04: severity 1 is informational only
AI_CONFIDENCE_THRESHOLD = 0.65      # FR-06: below this, route to manual review
MAX_RETRIES = 3                     # FR-09 / NFR-02
BACKOFF_BASE_MS = 200               # exponential backoff: 200, 400, 800 ms
PRICE_TOLERANCE = 0.01              # FR-12: storefront price may differ from ERP by at most 1%

# Simulated reliability of the Dealer Management System (DMS) connector
DMS_TRANSIENT_FAILURE_RATE = 0.04   # random timeouts on any single call
DMS_OUTAGE_WINDOWS = [               # planned or unplanned outages (dealer_id, start day, end day)
    ("D07", 40, 43),
    ("D03", 71, 72),
]

# Non-functional targets (NFR-01)
P95_LATENCY_TARGET_MS = 50

# Assumption used for the business case: average hours between a machine fault
# and parts being reserved in the current manual process (call dealer, technician
# visits, diagnoses, then orders). Validate this with dealer service managers.
BASELINE_FAULT_TO_RESERVATION_HOURS = 30
