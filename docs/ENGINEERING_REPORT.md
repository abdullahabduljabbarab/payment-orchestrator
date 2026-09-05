# Engineering Report

## What this is

A payment lifecycle orchestrator. It is the only service that talks to external payment providers and the only service that asks the ledger to move a customer's money. A payment is a two-phase movement of funds, reserve then capture or release, driven by an explicit state machine, so no failure can leave money in an impossible place. It settles against a live double-entry ledger, is resilient to provider timeouts, outages and duplicate callbacks, and publishes its lifecycle through a transactional outbox. The system is built around the failure cases that break real payment systems, not the happy path.

## Architecture

```
Client
  |
FastAPI + Pydantic (validation, routing, OpenAPI docs)
  |
Service layer (state machine, risk, reserve/capture/release, reconciliation)
  |         \
  |          --> Ledger API (reserve, capture, release transfers, idempotent)
  |          --> Providers (NorthPay, RapidPay, LegacyPay) behind a circuit breaker
  |
SQLAlchemy + Alembic (ORM, migrations)
  |
PostgreSQL 16 (Cloud SQL, europe-west2)
  |
Transactional outbox --> Pub/Sub topic payment-events (ABS event envelope)
```

Deployed on GCP Cloud Run with auto-scaling (0 to 3 instances). CI/CD via GitHub Actions: lint, test against a PostgreSQL service container, validate Terraform, deploy on green main. Infrastructure defined in Terraform: the orchestrator's database and user on the ledger's shared Cloud SQL instance, Artifact Registry, Cloud Run, Pub/Sub, Secret Manager, IAM.

## Key engineering decisions

**Reserve, capture, release.** Money moves in two phases through Payment Suspense and Settlement Clearing, so the window between asking a provider to move money and learning whether it did has an explicit financial state rather than an ambiguous one. Failure is a compensating transfer, never a deletion, which fits the append-only ledger.

**Provider never called before funds are reserved.** The state machine has no legal transition from an unreserved state to the provider call, so the worst inconsistency in payments, a provider moving money the customer cannot cover, is structurally impossible rather than guarded against (ABS-REQ-011).

**At-most-once ledger effect per operation.** Each reserve, capture and release carries a deterministic idempotency key, and the ledger returns the original transaction for a repeated key. A crash between the ledger committing and the orchestrator recording the result is safe: the retry returns the original transfer. This is stated per operation, not as exactly-once delivery, which no protocol provides (ABS-REQ-002, 005).

**Timeout is not failure.** A definitive failure may fall back to another provider; an ambiguous timeout pins the payment to its provider and reconciles, because the first provider may already have moved the money and a second attempt would move it twice (ABS-REQ-012).

**Duplicate callback protection.** Callbacks are deduplicated on `(provider, provider_reference)` with a unique database constraint, so a provider that confirms the same payment more than once produces a single capture (ABS-REQ-003).

**One state machine as the source of truth.** Every transition goes through a single guard that enforces the allowed-transition table, writes the append-only event and the outbox event, and commits, in one transaction. A payment is always left durable and legal, so crash recovery is resuming from the state on disk.

**Transactional outbox, at-least-once, stated honestly.** Events are written in the same transaction as the state change, so an event exists if and only if that change committed. The relay marks a row published only once the transport accepts it, stops at the first failure to preserve order, and reports which transport handled the batch, so it is never ambiguous whether a real publish occurred. Consumers deduplicate on `event_id` (ABS-REQ-007, 008, 009).

## Numbers

| Metric | Value |
|--------|-------|
| Test count | 63 |
| Alembic migrations | 2 (initial schema and the paymentstate enum, then the ABS event envelope) |
| API endpoints | 8 |
| Payment states | 13, with a single allowed-transition table |
| Simulated providers | 3, each with a distinct failure personality |
| ABS requirements owned | 6 (002, 003, 004, 005, 011, 012), plus event delivery (007, 008, 009) |

## Test categories

| Category | What they prove |
|----------|-----------------|
| State machine | Only legal transitions occur, terminal states have no exit, transient states always have a way out (parametrised over the transition table) |
| Service lifecycle | Risk allow/review/block, reserve, capture, release, timeout to UNKNOWN, fallback, reconcile, duplicate callback captures once |
| Ledger client | Reserve/capture/release build the correct transfers, keys are deterministic, insufficient balance and 5xx map to typed errors |
| Provider router and breaker | Breaker opens after threshold, resets on success, half-opens, and a payment routes past an open provider |
| Transactional outbox | Envelope carries the full contract, correlation propagates and causation chains, at-least-once with a stable dedup id, a failed publish leaves everything pending |
| API | Settle, over-limit rejection, invalid amount, not found, reconcile refused on a settled payment |
| Schema | The ORM and the migration agree on the enum labels |

## Injected failures tested

- Insufficient funds at reserve (the provider is never reached, no money moves)
- Provider decline (a compensating release returns the reservation to the customer)
- Provider timeout (the payment goes to UNKNOWN, pinned to its provider, no fallback)
- Provider unavailable (the circuit breaker opens and the payment falls back to the next provider)
- Every provider unavailable (the reservation is released so funds are not stranded)
- Ledger unavailable during reserve, capture or release (the payment is left retryable in that state, not lost)
- Triple duplicate callback (a single capture, no duplicate events)
- The enum name/value mismatch found on the first live deploy (see below)

## What the live deployment found

The first live payment returned 500. The `paymentstate` type is created by the migration from the enum's lowercase values, but the ORM defaulted to persisting the member names, which the live type rejected. It passed CI because the suite builds tables from the model metadata, which is self-consistent, while the live schema is built by Alembic, so only production ever exercised the two against each other. The fix persists the enum values, and a schema test now fails if the model and the migration ever diverge on the enum labels again. The wider point matches the ledger's hash-chain story: the defect was only reachable against the real, migrated database, and the fix closed the gap that let the two representations drift.

## Load and latency

The service was load tested against the live Cloud Run deployment. Configuration, results and the SLO targets are in [SLO.md](SLO.md).

## Cloud architecture

| Component | Service | Region |
|-----------|---------|--------|
| API runtime | Cloud Run | europe-west2 (London) |
| Database | Cloud SQL PostgreSQL 16 (shared with the ledger, own schema and user) | europe-west2 |
| Container registry | Artifact Registry | europe-west2 |
| Event bus | Pub/Sub | europe-west2 |
| Secrets | Secret Manager | europe-west2 |
| CI/CD | GitHub Actions | Ubuntu runners |
| IaC | Terraform | All orchestrator resources declared |

## V&V matrix

| Requirement | Verification method | Test / evidence |
|-------------|-------------------|-----------------|
| Provider never called before funds reserved | Automated test + live | `test_insufficient_funds_fails_and_never_reaches_provider`, live payment returning `failed` with provider null |
| At-most-once ledger effect per operation | Automated test | `test_keys_are_deterministic`, `test_idempotent_retry_creates_no_duplicate_events` |
| Ambiguous timeout pins and never falls back | Automated test | `test_timeout_pins_to_provider_and_does_not_fall_back` |
| UNKNOWN resolved by reconciliation | Automated test | `test_reconcile_unknown_success_settles`, `test_reconcile_unknown_failure_fails` |
| Duplicate callback deduplicated | Automated test + live | `test_duplicate_callback_captures_once`, live `duplicate` response |
| Fallback on definitive unavailability, breaker opens | Automated test + live | `test_unavailable_falls_back_to_next_provider`, `test_breaker_opens_after_threshold`, live fallback to RapidPay |
| Ledger unavailability leaves the payment retryable | Automated test | `test_ledger_unavailable_leaves_payment_reserving` |
| Compensating release on decline | Automated test + live | `test_provider_failure_releases_and_fails`, live release returning funds |
| Only legal state transitions occur | Automated test | `test_legal_transitions_allowed`, `test_illegal_transitions_rejected` |
| Event capture atomic, at-least-once, dedup on event_id | Automated test + live | `test_failed_publish_leaves_everything_pending`, `test_event_id_is_stable_for_consumer_dedup`, live Pub/Sub pull |
| Model and migration agree on schema | Automated test | `test_orm_enum_matches_initial_migration` |
| End-to-end settlement | Live verification | A real payment settling against the live ledger, Payment Suspense netting to zero |

## Design trade-offs

**Risk as a threshold stub vs. a real engine.** The risk decision is a deterministic threshold with the real allow/review/block contract, not a model. The contract and the state machine that consumes it are the engineering point; the decision moves to a dedicated risk engine later, which the orchestrator is already built to call.

**Synchronous processing vs. a background worker.** `POST /payments` runs the whole lifecycle synchronously and returns the terminal state, which is simple to reason about and to demonstrate. A production system with slow providers would move the provider call to a worker and drive the payment from callbacks; the state machine already supports that path (PROVIDER_PENDING, UNKNOWN, the callback endpoint).

**Outbox relay as an endpoint vs. a worker.** The relay is triggered via `POST /outbox/publish` rather than a continuously running process, which is simpler to deploy on Cloud Run and can be driven by Cloud Scheduler on a cron. The trade-off is slightly higher event delivery latency.

**Own database on the shared instance vs. a dedicated instance.** The orchestrator takes its own schema and user on the ledger's Cloud SQL instance rather than a second server. One instance is cheaper and smaller in surface, and the dependency on the ledger's instance is made explicit as a Terraform data source. A dedicated instance would isolate load, which is not needed at this scale.

**Simulated providers vs. real rails.** Three simulators with distinct failure personalities (unavailable-prone, reliable, timeout-prone) exercise the resilience logic deterministically in tests and stochastically in the live demo, without depending on a real provider sandbox.
