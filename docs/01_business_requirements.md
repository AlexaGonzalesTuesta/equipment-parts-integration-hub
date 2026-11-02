# 01 Business Requirements Document

**Project:** Connected Equipment Parts Integration Hub
**Author:** Alexa Gonzales Tuesta
**Status:** Version 1.0 (portfolio project, synthetic data)

## 1. Problem statement

A heavy equipment manufacturer sells machines through a network of independent dealers. When a machine
breaks down on a customer's job site, four systems hold part of the story, and none of them talk to each other:

| System | What it knows | Owner |
|---|---|---|
| Telematics platform | The machine reported a fault, when, how severe, and what the operator typed | Connected products team |
| ERP (order management) | Customers, machines, parts catalog, prices, fault-to-parts kits, sales orders | Order management / IT |
| Dealer Management System (DMS) | What parts each dealer has on the shelf | Each dealer |
| E-commerce storefront | Parts that customers order online themselves | Digital commerce team |

**Current state.** A fault alert reaches the customer, who calls the dealer. A technician drives out, diagnoses
the machine, then calls the parts counter, which checks stock and types an order into the ERP. Web orders arrive
in a separate queue and are re-keyed. Typical time from fault to parts reserved is more than a day
(assumption: 30 hours, to be validated with dealer service managers). Re-keying causes wrong parts, duplicate
orders, and no single place to see why an order is stuck.

**Goal.** Connect the four systems so a fault or a web order becomes a correct ERP order automatically, with the
nearest dealer stock reserved, and with every failure visible and recoverable.

## 2. Objectives and success measures

| Objective | KPI | Target | Result in simulation |
|---|---|---|---|
| Remove manual re-keying for machine faults | Touchless rate: actionable fault events ordered with no person | at least 85% | 94% |
| Reserve parts faster | Processing time per message, 95th percentile | under 50 ms | 1.7 ms |
| Prevent duplicate orders | Orders created for a resent message | 0 | 0 (70 duplicates blocked) |
| Keep wrong AI orders low | Precision of AI auto-orders on held-out data | at least 85% | 87% |
| Lose no messages | Messages without a final outcome | 0 | 0 |
| Recover from system outages without manual work | Outage messages recovered by replay | 100% | 100% (19 of 19) |

## 3. Stakeholders

| Stakeholder | Interest |
|---|---|
| Dealer service managers | Faster diagnosis, right parts on the truck the first time |
| Parts operations | Stock reserved at the right dealer, fewer emergency transfers |
| Order management (ERP team) | Clean, valid orders; no duplicates |
| E-commerce team | Web orders flow into ERP with correct prices |
| IT support | Can answer "where is my order?" in one lookup; can fix and replay failures |
| Cybersecurity | Validated inputs, no injection paths, no secrets in code |
| Customers | Less machine downtime |

## 4. Scope

**In scope:** inbound fault events and web orders; validation; enrichment from ERP master data; rule-based and
AI diagnosis; dealer stock sourcing and reservation; ERP order creation; retries; dead letter queue and replay;
audit trail; operations dashboard; automated tests.

**Out of scope (version 1):** shipping and invoicing, technician scheduling, pricing and discount logic beyond
list price, real system connections (systems are simulated with SQLite), user authentication on the API.

## 5. Requirements

The full catalog with stakeholder and priority is in [`requirements_catalog.csv`](requirements_catalog.csv).
Every requirement is linked to the automated tests that verify it in the
[traceability matrix](05_requirements_traceability.md).

### Functional
| ID | Requirement |
|---|---|
| FR-01 | Accept machine fault events from the telematics platform through a REST API. |
| FR-02 | Accept customer parts orders from the e-commerce storefront through a REST API. |
| FR-03 | Validate every inbound message against the canonical schema; reject malformed messages with a reason code. |
| FR-04 | Log severity 1 (informational) faults for monitoring without creating orders. |
| FR-05 | Map known fault codes to the parts kit defined in the ERP fault catalog. |
| FR-06 | When a fault code is missing or unknown, diagnose from the symptom note with the AI recommender; send predictions below the confidence threshold to a service advisor. |
| FR-07 | Source each line from the assigned dealer first, then another dealer in the same region, otherwise backorder. |
| FR-08 | Create exactly one ERP sales order per accepted message with header, lines, status and fulfillment type. |
| FR-09 | Retry transient DMS failures up to 3 times with exponential backoff. |
| FR-10 | Send unprocessable messages to a dead letter queue with a reason code; replay replayable ones once fixed. |
| FR-11 | Never create more than one order for the same message id. |
| FR-12 | Reject orders for parts not in the catalog; hold orders whose price differs from ERP by more than 1%. |
| FR-13 | Do not order the same kit again for the same machine and fault while an order from the last 7 days is open. |
| FR-14 | Record an audit trail of every processing step, retrievable by message id. |
| FR-15 | Provide an operations dashboard with volumes, outcomes, dead letters, fulfillment mix and AI performance. |

### Non-functional
| ID | Category | Requirement |
|---|---|---|
| NFR-01 | Performance | p95 processing latency under 50 ms per message across 1,000 messages. |
| NFR-02 | Reliability | No partial orders: if any step fails, all reservations for that message roll back. |
| NFR-03 | Data integrity | Inventory never negative; reserved never exceeds on hand. |
| NFR-04 | Security | Pattern and type validation on all inputs; parameterized SQL only; injection attempts rejected with no side effects. |
| NFR-05 | AI quality | AI auto-orders at least 85% precise on held-out data. |
| NFR-06 | Auditability | Every received message ends in exactly one terminal outcome. |
| NFR-07 | Maintainability | Business thresholds configurable in one file without changing processing code. |

## 6. User stories and acceptance criteria

**US-1. Proactive parts for a critical fault.** As a dealer service manager, I want parts reserved as soon as a
machine reports a critical fault, so my technician arrives with the right parts.
- Given a machine with a known fault code and severity 2 or 3, when the event arrives, then an ERP order with the
  catalog parts kit is created and stock is reserved at the machine's dealer (FR-05, FR-07, FR-08).
- Given the same event is sent twice, then only one order exists (FR-11).

**US-2. Diagnosis without a fault code.** As a service manager, I want older machines that only send a text note
to still get the right parts, but I do not want the system guessing when it is unsure.
- Given no fault code and a clear note, when the model is at least 65% confident, then an AI-assisted order is created
  and flagged as AI-assisted (FR-06).
- Given a vague note, then no order is created and the event appears in the review queue with the top 3 guesses (FR-06).

**US-3. Stock at another dealer.** As a parts manager, I want the system to find stock at a nearby dealer when mine
is out, so the customer is not backordered unnecessarily.
- Given the assigned dealer has no stock and another dealer in the same region does, then the line is reserved at
  that dealer with status TRANSFER (FR-07).

**US-4. Web order pricing.** As the pricing team, I want web orders with stale prices held, not booked.
- Given a storefront price more than 1% away from ERP, then the order is held with reason PRICE_MISMATCH (FR-12).

**US-5. Support can trace any order.** As IT support, I want to see every step a message went through, so I can
answer customer questions and fix failures quickly.
- Given any message id, the API and the console show the full audit trail and final status (FR-14).
- Given a dealer system outage, failed messages wait in the dead letter queue and are recovered by replay (FR-10).

## 7. Assumptions and open questions
- The 30 hour baseline from fault to parts reserved is an assumption and needs validation with dealers.
- One fault code maps to one standard parts kit. In reality kits may vary by machine model and serial range.
- Dealers in the same region can transfer parts within one day.
- Open question: should AI-assisted orders require dealer approval above a dollar amount?
