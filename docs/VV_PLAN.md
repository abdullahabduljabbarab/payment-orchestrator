# Verification and Validation Plan

## Approach

Every requirement this service owns is verified by an automated test running against a PostgreSQL database, and every behaviour that can be driven deterministically is additionally proven against the live Cloud Run deployment. CI runs the full suite (63 tests) against a PostgreSQL service container on every push, applies the Alembic migrations before the tests, lints with ruff, and validates the Terraform. The requirements are the ABS system requirements owned by the orchestrator, defined in [SYSTEM_REQUIREMENTS.md](https://github.com/abdullahabduljabbarab/abs-financial-systems/blob/main/SYSTEM_REQUIREMENTS.md).

## Requirement-to-Test Mapping

| Requirement | Verification | Test / Evidence |
|-------------|-------------|-----------------|
| ABS-REQ-002 (at-most-once financial effect per operation) | Automated | `test_keys_are_deterministic`, `test_idempotent_retry_creates_no_duplicate_events`, `test_duplicate_callback_captures_once` |
| ABS-REQ-003 (duplicate callback deduplicated) | Automated + Live | `test_duplicate_callback_captures_once`; live callback returning `duplicate` |
| ABS-REQ-004 (UNKNOWN resolved by reconciliation) | Automated | `test_reconcile_unknown_success_settles`, `test_reconcile_unknown_failure_fails` |
| ABS-REQ-005 (deterministic, idempotently keyed ledger transactions) | Automated | `test_keys_are_deterministic`, `test_reserve_builds_transfer_customer_to_suspense`, `test_capture_moves_suspense_to_settlement`, `test_release_returns_funds_to_customer` |
| ABS-REQ-011 (provider never called before funds reserved) | Automated + Live | `test_insufficient_funds_fails_and_never_reaches_provider`; live payment returning `failed` with provider null |
| ABS-REQ-012 (ambiguous timeout pins to provider, never falls back) | Automated | `test_timeout_pins_to_provider_and_does_not_fall_back` |
| ABS-REQ-007 (events emitted for meaningful transitions) | Automated + Live | `test_envelope_has_full_abs_contract`, `test_correlation_propagates_and_causation_chains`; live Pub/Sub pull |
| ABS-REQ-008 (at-least-once delivery, capture atomic with state change) | Automated | `test_failed_publish_leaves_everything_pending`, `test_retry_after_failure_publishes`, `test_publish_marks_rows_published` |
| ABS-REQ-009 (consumer deduplication on a stable id) | Automated | `test_event_id_is_stable_for_consumer_dedup` |

## Behavioural Coverage

| Behaviour | Verification | Test / Evidence |
|-----------|-------------|-----------------|
| Only legal state transitions occur; terminal states have no exit | Automated | `test_legal_transitions_allowed`, `test_illegal_transitions_rejected`, `test_terminal_states_have_no_exit`, `test_transient_states_are_not_terminal` |
| A new payment starts in RECEIVED | Automated | `test_create_payment_starts_received` |
| Risk holds high value for review, blocks over the limit, without reserving | Automated + Live | `test_high_value_goes_to_review_without_reserving`, `test_over_limit_is_rejected`; live `risk_review` and `rejected` |
| Reserve, capture and release map to the correct ledger transfers | Automated | `test_reserve_builds_transfer_customer_to_suspense`, `test_capture_moves_suspense_to_settlement`, `test_release_returns_funds_to_customer` |
| Insufficient balance and ledger 5xx map to typed errors | Automated | `test_insufficient_balance_maps_to_insufficient_funds`, `test_server_error_maps_to_unavailable` |
| Provider success settles; provider failure releases and fails | Automated + Live | `test_provider_success_settles`, `test_provider_failure_releases_and_fails`; live compensating release |
| Definitive unavailability falls back; all-unavailable releases the reservation | Automated | `test_unavailable_falls_back_to_next_provider`, `test_all_providers_unavailable_releases_reservation` |
| Circuit breaker opens, resets on success, half-opens, and skips open providers | Automated + Live | `test_breaker_opens_after_threshold`, `test_breaker_success_resets_count`, `test_breaker_half_opens_after_reset_window`, `test_router_skips_open_provider`; live fallback to RapidPay |
| Ledger unavailability leaves the payment retryable, not lost | Automated | `test_ledger_unavailable_leaves_payment_reserving` |
| Reconcile is refused on a non-UNKNOWN payment | Automated + Live | `test_reconcile_on_settled_payment_conflicts`; live `409` |
| The ORM and the migration agree on the schema | Automated | `test_orm_persists_enum_values_not_names`, `test_orm_enum_matches_initial_migration` |
| API contract: settle, rejection, validation, not-found | Automated | `test_post_payment_settles`, `test_post_payment_over_limit_rejected`, `test_invalid_amount_rejected`, `test_get_missing_payment_404` |
| Migrations apply cleanly on a fresh database | CI evidence | `alembic upgrade head` runs in CI before the suite, and on container start |
| Lint and infrastructure validity | CI evidence | ruff on push; `terraform fmt`, `init`, `validate` in the Terraform workflow |

## Live Verification

Driven against the deployed service and the live ledger, with screenshots and output in [README.md](../README.md) and [PRODUCTION_LOG.md](PRODUCTION_LOG.md):

- A real payment settled end to end: customer 1000.00 to 750.00, Payment Suspense netting to zero, Settlement Clearing 0 to 250.00, `received` to `settled` through the full machine.
- The six lifecycle events published to real Pub/Sub and pulled back from the subscription, each carrying the same `correlation_id`.
- Insufficient funds returning `failed` with the provider never reached; a duplicate callback returning `duplicate`; reconcile on a settled payment returning `409`.
- A compensating release returning a declined payment's funds to the customer, and traffic failing over from NorthPay to RapidPay after the breaker opened.

## Acceptance Criteria

The orchestrator passes V&V when:

- Every ABS requirement it owns has an automated test, and every deterministic behaviour is additionally shown live.
- CI is green: ruff, the full suite against PostgreSQL with migrations applied, and Terraform validation.
- The live `/health` returns 200 and a real payment settles end to end against the live ledger with the Payment Suspense account netting to zero.
