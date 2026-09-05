# Threat Model (STRIDE)

## Scope

This threat model covers the payment-orchestrator service: a payment lifecycle orchestrator deployed on GCP Cloud Run with Cloud SQL PostgreSQL, which settles against the ledger API and talks to external payment providers. It does not cover the ledger's own threat model, network-level DDoS, or physical security of cloud infrastructure.

## Assets

| Asset | Sensitivity | Location |
|-------|-------------|----------|
| Payment state and history | High | Orchestrator Cloud SQL |
| The reservation invariant (no provider called before funds reserved, no money moved for a declined provider) | High | Enforced by the state machine and the ledger |
| Ledger credential | Critical (moves money) | Secret Manager |
| Database credentials | Critical | Secret Manager |
| GCP service account key | Critical | GitHub Secrets |
| Outbox events | Medium (business facts) | Orchestrator Cloud SQL, Pub/Sub |

## Threat Analysis

### S: Spoofing

| Threat | Mitigation | Test |
|--------|------------|------|
| Attacker forges a provider callback to force a capture | The callback acts only on a payment in PROVIDER_PENDING or UNKNOWN, and is deduplicated on `(provider, provider_reference)`, so a forged or replayed callback is recorded and ignored rather than driving a second financial effect. Signature verification is a noted gap below. | `test_duplicate_callback_captures_once` |
| Attacker submits a payment request for an account | The reservation is made against the named account and checked by the ledger, so a payment cannot draw funds the account does not hold. Endpoint authentication is a noted gap below. | `test_insufficient_funds_fails_and_never_reaches_provider` |

### T: Tampering

| Threat | Mitigation | Test |
|--------|------------|------|
| Direct modification of payment state in the database | The money authority is the ledger, which is itself tamper-evident. The orchestrator keeps an append-only `payment_events` log, so a payment's history is reconstructable independently of the mutable current state. | `test_create_payment_starts_received` |
| Replaying an operation to move money twice | Each reserve, capture and release carries a deterministic idempotency key, so the ledger returns the original transfer for a repeated key rather than making a second one. | `test_keys_are_deterministic`, `test_idempotent_retry_creates_no_duplicate_events` |
| SQL injection via API parameters | All queries use the SQLAlchemy ORM with parameterised queries, and Pydantic validates and coerces every input before it reaches the service layer. | `test_invalid_amount_rejected` |

### R: Repudiation

| Threat | Mitigation | Test |
|--------|------------|------|
| Denial that a payment or transition occurred | Every transition is written to the append-only `payment_events` log, every provider interaction to `provider_attempts`, and every event carries `correlation_id` and `causation_id` so a payment's causal chain is reconstructable. | `test_correlation_propagates_and_causation_chains` |

### I: Information Disclosure

| Threat | Mitigation | Test |
|--------|------------|------|
| Enumeration of payment data | Payments, events and accounts are keyed by UUID, not sequential IDs, so records are not enumerable. Endpoint authentication is a noted gap. | Type-safe UUID parsing |
| Stack traces in error responses | FastAPI returns structured JSON errors; no stack traces or internal state in responses. | `test_get_missing_payment_404` |
| Credential leakage in logs | Structured JSON logging records metadata (payment id, state, path, timing), not request bodies or credentials. | Log format in `app/logging.py` |

### D: Denial of Service

| Threat | Mitigation | Test |
|--------|------------|------|
| Hammering a failing provider | Repeated definitive unavailability opens the provider's circuit breaker, so traffic routes to a healthy provider instead of retrying a dead one. | `test_breaker_opens_after_threshold`, `test_router_skips_open_provider` |
| Resource exhaustion via large outbox reads | The outbox endpoints are cursor-limited (max 200 per call). Cloud Run scales 0 to 3 instances with connection pooling. | Query limits on the outbox endpoints |
| Malicious payment payloads | Pydantic enforces a positive amount and valid types before the request reaches the database. | `test_invalid_amount_rejected` |

### E: Elevation of Privilege

| Threat | Mitigation | Test |
|--------|------------|------|
| Compromise of the ledger credential to move money freely | The credential is held in Secret Manager and injected at runtime, accessible only to the service's runner identity. Scoping it to a transfers-only ledger role rather than admin is a noted gap. | Secret Manager IAM in `terraform/` |
| Driving a payment through an unintended transition | Every state change goes through one guard enforcing the allowed-transition table, so an illegal transition is rejected rather than applied. | `test_illegal_transitions_rejected` |

## Mitigations Not Yet Implemented

| Gap | Risk | Priority |
|-----|------|----------|
| Signed provider webhooks on the callback endpoint | Medium: a forged callback is bounded by the state and dedup guards but not rejected outright | Would add in production |
| Authentication on the orchestrator's own endpoints, and per-account ownership | Medium: any caller can initiate a payment or query one | Would add in production |
| Scoped ledger service identity instead of an admin credential | Medium: a leaked credential can do more than post transfers | Would add in production |
| Rate limiting | Low: API abuse | Would add via Cloud Armor or middleware |

## Requirement-to-Test Traceability

| Requirement | Tests |
|-------------|-------|
| Provider never called before funds reserved | `test_insufficient_funds_fails_and_never_reaches_provider` |
| At-most-once ledger effect per operation | `test_keys_are_deterministic`, `test_idempotent_retry_creates_no_duplicate_events`, `test_duplicate_callback_captures_once` |
| Ambiguous timeout pins and never falls back | `test_timeout_pins_to_provider_and_does_not_fall_back` |
| Duplicate callback deduplicated | `test_duplicate_callback_captures_once` |
| Only legal state transitions occur | `test_legal_transitions_allowed`, `test_illegal_transitions_rejected` |
| Circuit breaker isolates a failing provider | `test_breaker_opens_after_threshold`, `test_router_skips_open_provider` |
| Events captured atomically and deduplicated by consumers | `test_failed_publish_leaves_everything_pending`, `test_event_id_is_stable_for_consumer_dedup` |
