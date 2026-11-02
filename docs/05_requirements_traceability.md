# 05 Requirements Traceability Matrix

Generated automatically by `tools/build_rtm.py` from the test run at 2026-11-02T21:24:12.

**22 of 22 requirements verified** by passing tests.

| ID | Priority | Requirement | Test levels | Tests | Passed | Status |
|---|---|---|---|---|---|---|
| FR-01 | Must | Accept machine fault events from the telematics platform through a REST API. | system | test_api_telematics_event_round_trip | 1 | VERIFIED |
| FR-02 | Must | Accept customer parts orders from the e-commerce storefront through a REST API. | integration, system | test_api_storefront_order_and_validation<br>test_web_order_creates_order_and_blocks_double_submit | 2 | VERIFIED |
| FR-03 | Must | Validate every inbound message against the canonical schema and reject malformed messages with a reason code. | system, unit | test_api_storefront_order_and_validation<br>test_malformed_event_is_rejected<br>test_malformed_web_order_is_rejected<br>test_valid_event_parses | 9 | VERIFIED |
| FR-04 | Must | Log severity 1 (informational) faults for monitoring without creating orders. | integration | test_informational_fault_does_not_create_order | 1 | VERIFIED |
| FR-05 | Must | Map known fault codes to the parts kit defined in the ERP fault catalog. | integration | test_known_fault_creates_order_with_catalog_kit | 1 | VERIFIED |
| FR-06 | Should | When a fault code is missing or unknown, diagnose from the symptom note with the AI recommender and send predictions below the confidence threshold to a service advisor for manual review. | integration, unit | test_ai_diagnoses_event_without_fault_code<br>test_confidence_threshold_is_configurable<br>test_low_confidence_goes_to_manual_review<br>test_recommender_diagnoses_clear_symptoms<br>test_recommender_is_unsure_about_vague_notes | 5 | VERIFIED |
| FR-07 | Must | Source each order line from the machine's assigned dealer first, then from another dealer in the same region, otherwise backorder it. | integration | test_backorder_when_region_is_out<br>test_regional_transfer_when_assigned_dealer_is_out<br>test_sources_from_assigned_dealer_first | 3 | VERIFIED |
| FR-08 | Must | Create exactly one ERP sales order per accepted message with header, lines, status and fulfillment type. | integration, system | test_known_fault_creates_order_with_catalog_kit<br>test_orders_match_outcomes_one_to_one<br>test_web_order_creates_order_and_blocks_double_submit | 3 | VERIFIED |
| FR-09 | Must | Retry transient Dealer Management System failures up to 3 times with exponential backoff. | integration, system | test_dead_letter_after_retries_then_replay<br>test_outage_messages_recovered_by_replay<br>test_retry_recovers_from_transient_failures | 3 | VERIFIED |
| FR-10 | Must | Send messages that cannot be processed to a dead letter queue with a reason code and allow replay of replayable messages once the cause is fixed. | integration, system | test_dead_letter_after_retries_then_replay<br>test_outage_messages_recovered_by_replay<br>test_unknown_machine_is_replayable_dead_letter | 3 | VERIFIED |
| FR-11 | Must | Never create more than one order for the same message id (idempotency for resends and double clicks). | integration, system | test_duplicate_message_creates_one_order<br>test_orders_match_outcomes_one_to_one<br>test_web_order_creates_order_and_blocks_double_submit | 3 | VERIFIED |
| FR-12 | Must | Reject storefront orders for parts not in the ERP catalog and hold orders whose price differs from the ERP price list by more than 1 percent. | integration | test_price_mismatch_is_held<br>test_price_within_tolerance_is_accepted<br>test_unknown_part_is_rejected | 3 | VERIFIED |
| FR-13 | Should | Do not create a second order for the same machine and fault while an order from the last 7 days is still open. | integration | test_open_order_for_same_fault_is_not_duplicated | 1 | VERIFIED |
| FR-14 | Must | Record an audit trail of every processing step that can be retrieved by message id. | integration, system | test_api_telematics_event_round_trip<br>test_audit_trail_records_each_step | 2 | VERIFIED |
| FR-15 | Should | Provide an operations dashboard with volumes, outcomes, dead letters, fulfillment mix and AI performance. | system | test_dashboard_renders_without_errors | 1 | VERIFIED |
| NFR-01 | Must | 95th percentile processing latency is under 50 ms per message across 1000 messages. | nonfunctional | test_p95_latency_under_target | 1 | VERIFIED |
| NFR-02 | Must | No partial orders: if any step fails all inventory reservations for that message roll back. | nonfunctional | test_failure_mid_order_rolls_back_all_reservations | 1 | VERIFIED |
| NFR-03 | Must | Inventory never goes negative and reserved quantity never exceeds on-hand quantity. | system | test_inventory_invariants_hold_after_full_run | 1 | VERIFIED |
| NFR-04 | Must | Inputs are validated by pattern and type, all database access is parameterized, and injection attempts are rejected without side effects. | nonfunctional, unit | test_injection_attempts_are_rejected_without_side_effects<br>test_malformed_event_is_rejected<br>test_no_string_formatted_sql_in_source | 8 | VERIFIED |
| NFR-05 | Must | AI diagnoses that create orders automatically are at least 85 percent precise on held-out data. | unit | test_ai_auto_order_precision_meets_target | 1 | VERIFIED |
| NFR-06 | Must | Every received message ends in exactly one terminal outcome; no message is lost. | system | test_every_message_reaches_one_terminal_outcome | 1 | VERIFIED |
| NFR-07 | Should | Business rule thresholds are configurable in one place without code changes to processing logic. | integration | test_confidence_threshold_is_configurable | 1 | VERIFIED |
