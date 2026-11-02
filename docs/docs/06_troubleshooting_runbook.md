# 06 Troubleshooting Runbook

For IT support. Start every investigation with the message id (event id or web order id).

## Step 1. Find the message
- Console: **Trace a message** tab, paste the id.
- API: `GET /v1/messages/{message_id}` returns the final status and every step with timestamps.
- 404 means the hub never received it: the problem is upstream (sending system or network).

## Step 2. Read the final status

| Status | Meaning | Action |
|---|---|---|
| ORDER_CREATED | Order exists in ERP | Give the customer the order id. If status is BACKORDERED, parts operations owns it. |
| DUPLICATE | A resend of a message already processed | Nothing. Look up the original. |
| NO_ACTION | Severity 1, or an open order already covers this fault | Explain the rule; the existing order id is in the detail. |
| MANUAL_REVIEW | AI was not confident | Service advisor reviews the top 3 guesses in the review queue. |
| DEAD_LETTER | Could not be processed; see reason code | Follow the playbook below. |
| REJECTED | Bad input that will never succeed as sent | Tell the sending team. Do not replay. |

## Step 3. Playbooks by reason code

### DMS_UNAVAILABLE
1. Check the audit trail: three `RETRY`/`FAILED` entries against one dealer means that dealer's system is down.
2. Check `GET /v1/dead-letters` for other messages on the same dealer. Several at once confirms an outage.
3. Confirm with the dealer that their system is back.
4. `POST /v1/dead-letters/replay`. The response shows how many recovered.
5. Nothing to clean up: the failed attempt rolled back, so no partial reservations exist.

### MASTER_DATA_MISSING
1. The machine, customer or dealer id is not in ERP (common for a newly sold machine).
2. Ask the master data team to create the record.
3. Replay. The message processes normally.

### VALIDATION_FAILED
1. The detail shows the field and rule, for example `severity: Input should be less than or equal to 3`.
2. Raise a defect with the sending system's team, with the payload from the trace.
3. Do not replay; the same payload will fail again.

### PRICE_MISMATCH
1. The storefront showed a price that no longer matches ERP (usually a stale cached cart).
2. Pricing team confirms the correct price with the customer, then the order is resubmitted.

### UNKNOWN_PART
1. The part is not in the ERP catalog (discontinued or typo).
2. E-commerce team removes it from saved carts and suggests the replacement part.

## Step 4. Health checks
- `GET /health` should return `ok`.
- Console **Integration health** tab: a spike in DMS retries for one dealer is an early warning before dead letters appear.
- p95 processing time above 50 ms breaches NFR-01; check database size and connector response times.

## Escalation
| Situation | Escalate to |
|---|---|
| One dealer's DMS down more than 4 hours | Dealer IT contact |
| Many VALIDATION_FAILED from one source | Owning team of that system, as a defect |
| AI review queue growing faster than advisors clear it | Data science: review threshold and retrain on recent notes |
