# 04 System Test Plan

## 1. Objective
Prove that the integration hub meets every functional and non-functional requirement in the
[business requirements](01_business_requirements.md) before release, and keep proving it on every code change.

## 2. Test levels

| Level | File | What it covers | Count |
|---|---|---|---|
| Unit | `tests/test_unit.py` | Schema validation rules, AI model behavior and quality | 11 |
| Integration | `tests/test_integration.py` | Hub with ERP and DMS connectors: kits, sourcing, retries, dead letters, idempotency, pricing, audit | 18 |
| Non-functional | `tests/test_nonfunctional.py` | Performance, rollback on failure, injection attempts, parameterized SQL | 6 |
| System (end to end) | `tests/test_system.py` | Full 3,800-message run, REST API round trips, dashboard renders | 7 |

Every test is tagged with the requirement it verifies, for example `@pytest.mark.req("FR-07")`.
`tools/build_rtm.py` turns the test results into the [traceability matrix](05_requirements_traceability.md) and
fails the build if any Must-have requirement has no passing test.

## 3. Test techniques used
- **Equivalence partitioning and boundary values:** severity 1 vs 2 vs out of range 7; price at +0.5% (accept) vs
  -20% (hold) around the 1% tolerance; quantity 0.
- **Negative testing:** malformed ids, unknown machines, unknown parts, injection strings.
- **Fault injection:** DMS stubs that time out N times, or drop the connection between two reservations, to prove
  retries, dead lettering and rollback.
- **State-based testing:** the same machine and fault on day 0, day 2 and day 19 (7-day rule).
- **Load testing:** 1,000 real messages from the feed with realistic random DMS timeouts, p95 measured.
- **Invariant checks after a full run:** no negative stock, no duplicate orders, no lost messages.

## 4. Test data
Synthetic and reproducible (seed 42): 12 dealers, 300 customers, 1,500 machines, 56 parts, 27 fault codes,
4,000 labeled history notes, 3,051 telematics events and 814 web orders. The feeds intentionally include
duplicates, malformed payloads, unknown machines, missing fault codes, stale prices, discontinued parts and two
dealer system outages. Each test gets its own copy of the database, so tests never affect each other.

## 5. Environments
| Environment | Purpose |
|---|---|
| Local | Developer runs `pytest` before every commit |
| GitHub Actions (CI) | Runs the pipeline, all tests and the traceability check on every push and pull request |

## 6. Entry and exit criteria
**Entry:** code compiles, pipeline runs end to end, test data generated.
**Exit (release ready):**
- 100% of tests pass.
- Every Must-have requirement is VERIFIED in the traceability matrix.
- p95 latency under 50 ms; AI auto-order precision at least 85%.
- No open defects of severity High or Critical.

## 7. Defect management
Severity: Critical (data loss, wrong order shipped), High (requirement not met), Medium (workaround exists),
Low (cosmetic). Defects are logged as GitHub Issues with steps to reproduce, expected vs actual result, the
message id, and the audit trail.

### Defects found during development
| ID | Found by | Description | Fix |
|---|---|---|---|
| DEF-01 | System test | Metrics counted a resent message's DUPLICATE result as its final outcome, which hid the original order and made order counts disagree with outcomes. | Final outcome now ignores duplicate resends; duplicates counted separately. |
| DEF-02 | System test (API) | An API test event was suppressed by the 7-day rule because the simulated history already had an open order for that machine and fault. Correct behavior, wrong test data. | Test now uses a date outside the 7-day window. |
| DEF-03 | Run review | Without restocking, inventory drained over 90 days and 65% of orders were backordered, which is unrealistic. | Added a nightly DMS batch: reserved parts ship, low stock is replenished with lead time. |
| DEF-04 | Code review | Supplier replenishment used SQLite `random()`, so runs were not reproducible. | Moved randomness into the seeded connector. |

## 8. Regression
The full suite runs in under 30 seconds, so every test runs on every push. No manual regression pass is needed.
