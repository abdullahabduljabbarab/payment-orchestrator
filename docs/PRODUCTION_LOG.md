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

## Milestone 6

### Goal
Deploy the orchestrator live, prove the full path end to end against the live ledger, and codify the infrastructure as Terraform.

### Completed
- Live infrastructure on the ledger's GCP project: the orchestrator's own `payments` database and `orchestrator` user on the shared Cloud SQL instance, an Artifact Registry repository, Secret Manager secrets for the database URL and the ledger credential injected at runtime, the `payment-events` topic and subscription, and a Cloud Run service. A deploy job in CI builds, pushes and rolls out on every push to `main`.
- Terraform codifying the orchestrator's slice of the project (`main.tf`, `variables.tf`, `outputs.tf`): the database and user, Artifact Registry, both secrets and their access grants, a dedicated least-privilege runner service account, the topic, subscription and publisher grant, and the Cloud Run service. It references the ledger's Cloud SQL instance through a data source rather than creating a second server, so the dependency is explicit: the ledger is applied first, the orchestrator layers on top.
- A `terraform` CI workflow (`fmt -check`, `init`, `validate`) matching the ledger's.
- Test count: 61 to 63, a schema regression test.

### Problems / Decisions
- The first live payment returned 500. The `paymentstate` type is created by migration 001 from the enum's lowercase values (`received`), but the ORM's `Enum(PaymentState)` column defaulted to persisting the member names (`RECEIVED`), which the live type rejected with `invalid input value for enum paymentstate: "RECEIVED"`. It passed CI because the suite builds tables from `Base.metadata`, which is self-consistent (names on both sides), while the live schema is built by Alembic (values), so only production ever exercised the ORM and the migration against each other. Fixed with `values_callable` so the ORM emits the values, and added a schema test that fails if the model and the initial migration ever diverge on the enum labels again. No data migration was needed: the live type was already correct and held no rows.
- The orchestrator takes its own database and user on the ledger's existing Cloud SQL instance rather than standing up a second server. One instance, isolated schemas, smaller surface, and the shared-instance dependency is expressed as a Terraform data source.
- The infrastructure was bootstrapped by CLI during the first deploy and then codified as Terraform, matching the ledger. The Terraform is the reproducible definition of the stack and declares a dedicated runner service account as the intended least-privilege runtime identity.

### Evidence
- Live `GET /health` returns 200 with the database connected. A real payment settled end to end against the live ledger: the customer moved from 1000.00 to 750.00, Payment Suspense netted to zero (reserve moved funds in, capture moved them out), and Settlement Clearing moved from 0 to 250.00. The payment traversed the full machine, `received` to `settled` through risk, reserve, provider and capture, routed via NorthPay, with the reserve and capture ledger transactions both recorded on the payment.
- Outbox and messaging: six events were written in the same transactions as the state changes, published to real Pub/Sub (`published: 6, failed: 0, transport: pubsub`) and the pending queue drained to zero. All six were then pulled from `payment-events-sub`, each carrying the same `correlation_id`: the full causal trace of one payment, live across the two services and the broker.
- 63/63 tests green, ruff clean, `terraform fmt` clean.

### Next
Prove the failure and edge paths live (UNKNOWN to reconcile, duplicate callback dedup, ledger-unavailable retry), then write the README with the evidence.

## Milestone 7

### Goal
Prove the failure and edge paths against the live deployment, bring the API docs to the ledger's standard, and write the README with the evidence.

### Completed
- Live failure and edge proofs against the deployment: insufficient funds fails before any provider is called, high value is held for review, over-limit is rejected, a duplicate callback is deduplicated, reconcile is refused on a settled payment, a declining provider triggers a compensating release that returns the funds, and traffic fails over from the primary provider when its breaker opens.
- OpenAPI metadata brought up to the ledger's standard: a description covering the reserve, capture and release model and the correctness and resilience properties, endpoint tags grouped into System, Payments, Reconciliation, Provider Callbacks and Event Delivery with per-endpoint summaries, and an MIT license link, so the interactive reference reads as a first-class API surface rather than an unlabelled default group.
- README with the full evidence: the Swagger walkthrough (settle, state history, duplicate dedup, reconcile guard, insufficient funds), the live GCP infrastructure (Cloud Run, logs, Pub/Sub, Cloud SQL, Artifact Registry, Secret Manager), the causal event chain pulled straight from the subscription, and a requirement-to-test matrix over the suite.
- MIT LICENSE.

### Problems / Decisions
- The stochastic provider paths (timeout to UNKNOWN to reconcile, all-providers-unavailable) are proven deterministically in the test suite rather than live, because the random simulators and a healthy ledger will not produce an injected timeout or outage on demand. The deterministic edges were driven live instead, and the natural compensating release and breaker fallback were caught by running a batch of live payments.

### Evidence
- Insufficient funds returns `failed` with the provider never reached and no money moved; a repeated callback returns `duplicate`; reconcile on a settled payment returns `409`; a live compensating release returned a declined payment's funds to the customer, leaving the balance whole; of 29 payments in one pass, six were served by the fallback provider after the primary's breaker opened.
- 63/63 tests green.

### Next
Continue the ecosystem: the risk engine, which the orchestrator's allow, review and block contract is already built to hand off to, then notification, analytics and the shared platform infrastructure.

## Milestone 8

### Goal
Integrate the risk engine: replace the local threshold stub with a call to the deployed engine, holding a payment for review when the engine is unreachable.

### Completed
- A `RiskClient`, in the shape of the ledger client: `POST /risk/evaluate` keyed on a deterministic `evaluation_id` per payment, so a retry after a crash returns the original decision rather than making a second one. A connection error or a 5xx raises `RiskUnavailable`.
- `process_payment` now asks the engine for the decision instead of a local threshold. The threshold stub and its constants are gone; the engine is the single source of the decision.
- Fail to review: if the engine is unreachable or times out, the payment is held in `RISK_REVIEW`, never allowed and never auto-rejected.
- The engine URL injected as `RISK_BASE_URL` in CI and Terraform.
- Test count: 67 to 72 (a fail-to-review test, and the risk client's response mapping and deterministic key).

### Problems / Decisions
- Strict fail-to-review, not a local fallback. When the engine is unreachable the orchestrator does not fall back to its old thresholds; that would reintroduce a second decision path that could fail open. Holding for review is the only safe default, the same shape as the reservation rule: uncertainty never moves money (ABS-REQ-013).
- The risk call is idempotent on a deterministic `evaluation_id` derived from the payment id, so crash recovery re-issues the same evaluation and the engine returns the original decision.

### Evidence
- 72/72 tests: a payment whose engine decision is review is held without reserving, block is rejected, allow settles, and an unreachable engine holds the payment for review. The risk client maps a 200 to a decision and a 5xx or connection error to unavailable, and the evaluation id is deterministic per payment.

### Next
Deploy and prove the full live path: a payment whose risk decision comes from the live engine, and the fail-to-review behaviour when the engine is unreachable.
