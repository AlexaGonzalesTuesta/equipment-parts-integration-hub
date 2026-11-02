# 03 Process Flows

## Current state: machine fault to parts order (manual)

```mermaid
flowchart TD
    A[Machine reports fault in telematics] --> B[Customer sees alert]
    B --> C[Customer calls dealer]
    C --> D[Dealer schedules technician]
    D --> E[Technician drives to site and diagnoses]
    E --> F[Technician calls parts counter]
    F --> G{Parts in stock at this dealer?}
    G -->|Yes| H[Counter re-keys order into ERP]
    G -->|No| I[Counter phones other dealers]
    I --> H
    H --> J[Parts reserved]
    style E fill:#fde2d6
    style F fill:#fde2d6
    style I fill:#fde2d6
```
Pain points (shaded): waiting for a site visit before parts are ordered, phone calls to find stock, re-keying errors.
Estimated 30 hours from fault to parts reserved (assumption to validate).

## Future state: through the integration hub

```mermaid
flowchart TD
    A[Machine reports fault] --> B[Hub validates and checks for duplicates]
    B --> C{Severity 2 or 3?}
    C -->|No| C1[Log for monitoring]
    C -->|Yes| D{Fault code in catalog?}
    D -->|Yes| F[Look up parts kit]
    D -->|No| E[AI diagnoses from operator note]
    E --> E1{Confident at least 65%?}
    E1 -->|No| E2[Service advisor review queue]
    E1 -->|Yes| F
    F --> G{Order already open for this fault in last 7 days?}
    G -->|Yes| G1[No new order]
    G -->|No| H[Reserve stock: assigned dealer, then region, else backorder]
    H --> I[ERP order created]
    I --> J[Technician dispatched with parts already reserved]
```

## Future state: e-commerce order

```mermaid
flowchart TD
    A[Customer submits web order] --> B[Validate and check for double submit]
    B --> C{Customer and dealer in ERP?}
    C -->|No| X1[Dead letter: master data, replay after fix]
    C -->|Yes| D{All parts in catalog?}
    D -->|No| X2[Reject: unknown part]
    D -->|Yes| E{Price within 1% of ERP?}
    E -->|No| X3[Hold: price mismatch for pricing team]
    E -->|Yes| F[Reserve stock and create ERP order]
```

## Support flow: a message failed

```mermaid
flowchart LR
    A[Alert or customer call] --> B[GET /v1/messages/id<br/>or console Trace tab]
    B --> C{Reason code}
    C -->|DMS_UNAVAILABLE| D[Confirm dealer system is back] --> R[Replay]
    C -->|MASTER_DATA_MISSING| E[Master data team adds record] --> R
    C -->|VALIDATION_FAILED| F[Raise defect with sending system]
    C -->|PRICE_MISMATCH| G[Pricing team contacts customer]
```
