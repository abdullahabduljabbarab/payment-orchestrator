# Production Log

Build and decision log for the payment orchestrator. Newest milestone last.

## Milestone 1

### Goal
Scaffold the service, the payment state machine, the data model, and the idempotent ledger client that the whole orchestrator is built on.

### Completed
- Payment state machine as the single source of truth: `PaymentState`, the full transition table, an `assert_transition` guard, and terminal states. Nothing advances a payment except through the guard, so an illegal move is caught immediately rather than discovered as a corrupt payment later.
- Data model: `payments`, `payment_events` (append-only history), `provider_attempts` (unique on `(provider, provider_reference)`), `outbox_events`.
- Ledger client with the three idempotent operations, `reserve`, `capture`, `release`, keyed deterministically (`payment:{id}:reserve|capture|release`), mapping insufficient balance to `InsufficientFunds` and 5xx to `LedgerUnavailable`.
- Config, database session, Pydantic schemas.
- Test count: 30 (state machine and ledger client via a mock HTTP transport).

### Problems / Decisions
- The state machine encodes ABS-REQ-011 structurally: there is no legal transition from an unreserved state to the provider call, so the provider cannot be reached before funds are reserved.
- The ledger client only ever moves money account to account; reserve, capture and release are the only shapes it can produce, so financial movement stays auditable and idempotent.

### Evidence
- 30/30 tests green; ruff clean.

### Next
The orchestration service and the reserve flow end to end.

## Milestone 2

### Goal
The orchestration service and the reserve slice: create a payment, run risk, and reserve funds against the ledger.

### Completed
- `_advance`, the one transition helper: it enforces the state machine, writes the append-only `payment_events` row and the outbox event in the same database transaction, and commits. A payment is always left durable and legal, which makes crash recovery a matter of resuming from the state on disk.
- `create_payment`, `process_payment` (risk stub returning allow/review/block, then reserve with insufficient-funds and ledger-unavailable handling).
- Endpoints: `POST /payments`, `GET /payments/{id}`, `GET /payments/{id}/events`, `GET /health`.
- Structured JSON logging.
- Test count: 30 to 41.

### Problems / Decisions
- Risk is a deterministic threshold stub for now. The real decision moves to the risk engine later; the orchestrator's contract with it is already the allow/review/block decision.
- Each state change commits on its own, so the payment is durably in, for example, RESERVING before the ledger call. On a crash the reserve is re-issued, and the deterministic idempotency key makes the retry safe.

### Evidence
- 41/41 tests green. Reserve slice runs: insufficient funds ends at FAILED with the provider never reached, a ledger outage leaves the payment safely in RESERVING, high value goes to RISK_REVIEW, over-limit is REJECTED.

### Next
Providers, capture and release, reconciliation, and the resilience behaviour.

## Milestone 3

### Goal
The full lifecycle: provider simulators, the provider call, capture and release, UNKNOWN reconciliation, fallback, circuit breaker, and the timeout-pinning invariant.

### Completed
- Providers: the `Provider` interface, `ScriptedProvider` for deterministic tests, and the three simulators (NorthPay unavailable-prone, RapidPay reliable, LegacyPay timeout-prone), producing SUCCESS, FAILED, TIMEOUT or UNAVAILABLE.
- Router with a circuit breaker (open, half-open, reset) and fallback provider selection.
- `submit_to_provider`, `_capture`, `_release`, `reconcile_payment`, `handle_callback`.
- Endpoints: `POST /payments/{id}/reconcile`, `POST /payments/{id}/callback`.
- Test count: 41 to 54.

### Problems / Decisions
- An ambiguous timeout pins the payment to its provider and never falls back (ABS-REQ-012). Falling over from a timeout could move the same money twice, because the first provider may already have processed it.
- Fallback happens only on a definitive UNAVAILABLE. A definitive FAILED is not a circuit-breaker failure: the provider is working, it just declined this payment, so only UNAVAILABLE opens the breaker.
- Duplicate callbacks are deduplicated on `(provider, provider_reference)` with a unique constraint (ABS-REQ-003), so a provider that sends the same confirmation more than once produces a single capture. Capture and release are also idempotent at the ledger, so duplicates are harmless at two layers.
- Failure is a compensating transfer back to the customer, not a deletion, which fits the append-only ledger.

### Evidence
- 54/54 tests: success to SETTLED, failure released to FAILED, timeout to UNKNOWN pinned with no fallback, unavailable falling back to the next provider, all-unavailable releasing the reservation, reconciliation to capture or release, a triple duplicate callback producing one capture, and the circuit breaker opening.

### Next
Make it deployable: migrations, container, CI.

## Milestone 4

### Goal
Bring the service up to a deployable, CI-tested standard.

### Completed
- Alembic migrations (`env.py`, `001_initial`) creating all four tables and the enum, verified to upgrade and downgrade cleanly.
- Dockerfile and `start.sh` (migrate then serve).
- CI: lint, then `alembic upgrade head` and the full test suite against a PostgreSQL service container.

### Problems / Decisions
- CI failed twice with `ModuleNotFoundError: No module named 'app'`, first in the alembic step and then in the pytest step. The cause was that locally the commands were run as `python -m alembic` and `python -m pytest`, which put the working directory on the import path, while CI runs the bare `alembic` and `pytest` console scripts, which do not. Fixed at the source: `env.py` puts the project root on `sys.path`, and `pyproject.toml` sets `pythonpath = ["."]` for pytest. Verified by reproducing the bare console-script invocations locally rather than relying on `python -m`.

### Evidence
- Migration upgrades and downgrades cleanly via the bare `alembic` console script; CI green (lint, migrate, test).

### Next
Event publishing to Pub/Sub using the ABS envelope.

## Milestone 5

### Goal
Publish outbox events to the `payment-events` topic using the full ABS event envelope, not a mechanical copy of the ledger relay.

### Completed
- Two transports selected by `PUBSUB_TOPIC`: Pub/Sub in the cloud, a log transport locally, so the exact relay path runs in tests without cloud credentials.
- The relay wraps every outbox row in the full envelope (`event_id`, `event_type`, `event_version`, `occurred_at`, `producer`, `correlation_id`, `causation_id`, `aggregate_id`, `payload`), publishes oldest first, and stops at the first failure so ordering holds and the failed row and everything after it stay pending.
- `causation_id` added to the outbox (migration 002) and populated so events form a real causal chain: received causes approved causes reserved, and so on.
- Endpoints: `GET /outbox/pending`, `POST /outbox/publish`.
- Test count: 54 to 61.

### Problems / Decisions
- The guarantee is at-least-once, stated honestly. A row is marked published only after the transport accepts it, so a publish failure leaves it pending for retry, and consumers deduplicate on `event_id`, which is the durable outbox row id.
- The envelope carries `correlation_id` and `causation_id` so a single payment can be traced end to end across services, which is what the ledger's simpler event shape does not yet do.

### Evidence
- 61/61 tests: envelope has the full contract, correlation propagates and causation chains event to event, publish marks rows published, a failed publish leaves everything pending, a retry succeeds, `event_id` is stable for dedup, and an idempotent workflow retry (a triple duplicate callback) creates no duplicate captured or settled events.
- Migration chain 001 to 002 verified via the bare `alembic` console script.

### Next
Deploy and integrate live: Cloud SQL database, Cloud Run service, Secret Manager configuration, the `payment-events` Pub/Sub infrastructure, and a live connection to the ledger. The Payment Suspense and Settlement Clearing accounts are provisioned on the ledger side, since the ledger owns financial state; the orchestrator receives only their account IDs and settles through the ledger API, never touching the ledger database. Then prove the full path end to end and write the README.
