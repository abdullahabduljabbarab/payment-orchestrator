# Payment Orchestrator

[![CI](https://github.com/abdullahabduljabbarab/payment-orchestrator/actions/workflows/ci.yml/badge.svg)](https://github.com/abdullahabduljabbarab/payment-orchestrator/actions/workflows/ci.yml)
[![Terraform](https://github.com/abdullahabduljabbarab/payment-orchestrator/actions/workflows/terraform.yml/badge.svg?branch=main)](https://github.com/abdullahabduljabbarab/payment-orchestrator/actions/workflows/terraform.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

A payment lifecycle orchestrator. It is the only service that talks to external payment providers and the only service that asks the ledger to move a customer's money. A payment is a two-phase movement of funds, reserve then capture or release, driven by an explicit state machine, so no failure can leave money in an impossible place. It settles against a live [double-entry ledger](https://github.com/abdullahabduljabbarab/ledger-api), routes across three providers behind a circuit breaker, publishes its lifecycle through a transactional outbox to Pub/Sub, and is deployed on Google Cloud Run with its infrastructure defined as code in Terraform.

It is one service in [ABS Financial Systems](https://github.com/abdullahabduljabbarab/abs-financial-systems): one authoritative ledger, specialised subsystems around it, explicit contracts between them, and evidence under failure.

**Live service**

- Interactive API reference (Swagger UI): https://payment-orchestrator-eppidgbmxa-nw.a.run.app/docs
- Health probe: https://payment-orchestrator-eppidgbmxa-nw.a.run.app/health

![Swagger overview](docs/images/01-swagger-overview.png)

---

## Contents

- [What it does](#what-it-does)
- [The financial model](#the-financial-model)
- [Correctness and resilience](#correctness-and-resilience)
- [Event delivery](#event-delivery)
- [Architecture](#architecture)
- [Verification and evidence](#verification-and-evidence)
- [Running it locally](#running-it-locally)
- [Design decisions](#design-decisions)
- [Project layout](#project-layout)

---

## What it does

The orchestrator owns everything between a customer requesting a payment and that payment being settled in the ledger. It runs a payment through risk, reserves the funds, calls a provider, and captures or releases depending on the outcome. Every step is a transition in a state machine, and the whole history of a payment is reconstructable from an append-only event log.

The reason the service exists is the space between "a provider was asked to move money" and "we know whether it did." That is where real payment systems break. A timeout is not a failure. A duplicate callback is not a second payment. A ledger that is briefly unavailable is not a lost settlement. And money must never leave a customer's account for a provider that then declines, nor be sent to a provider before the customer is known to be able to cover it. The orchestrator is built around those cases, not the happy path.

**A payment settling, end to end.** A `POST /payments` runs the full lifecycle synchronously and returns the terminal state. The response carries the two ledger transactions that make up the settlement, the reserve and the capture:

![Payment settled](docs/images/02-payment-settled.png)

**Its full history.** Every transition is written to an append-only log, so a payment can be replayed exactly:

![Payment events](docs/images/03-payment-events.png)
![Payment events continued](docs/images/03b-payment-events-tail.png)

The API also exposes the payment's current state, a reconciliation endpoint for payments left ambiguous by a provider timeout, a provider callback endpoint, and the outbox relay.

---

## The financial model

A payment is not a single ledger write. It is a two-phase movement of money through three positions, so that every stage has an explicit financial meaning and no stage can leave money stranded.

```
   Customer Account
        |
        |  reserve      (after approval, before any provider call)
        v
   Payment Suspense
        |
        |  capture      (on confirmed provider success)
        v
   Settlement Clearing
```

Payment Suspense and Settlement Clearing are system accounts owned by the ledger. Each of the three operations is an ordinary ledger transfer:

| Operation | Ledger transfer | When |
|-----------|-----------------|------|
| reserve | Customer to Payment Suspense | after approval, before any provider call |
| capture | Payment Suspense to Settlement Clearing | on confirmed provider success |
| release | Payment Suspense to Customer | on confirmed provider failure |

The failure path is a compensating transfer, not an undo. Because the ledger is append-only, releasing a reservation does not delete it; it adds an equal and opposite transfer, and both remain permanently visible in history.

The crucial ordering is that **the provider is never called unless the reservation succeeded.** That removes the worst inconsistency in payments, where a provider moves money and the ledger then finds the customer could not cover it. The full model, the complete state machine, the provider personalities and the guarantee are written up in [`DESIGN.md`](docs/DESIGN.md).

---

## Correctness and resilience

These behaviours were exercised against the live deployment, not just asserted in tests.

**Reservation before provider (ABS-REQ-011).** A payment the customer cannot cover is failed at the reserve step, and the provider is never contacted. The response state is `failed` with `provider`, `reserve_tx_id` and `capture_tx_id` all null, and no money moved:

![Insufficient funds](docs/images/06-insufficient-funds.png)

**Duplicate callback protection (ABS-REQ-003).** Provider callbacks are deduplicated on `(provider, provider_reference)` with a unique database constraint. The same callback sent twice is recorded once and rejected the second time, so a provider that confirms the same payment more than once produces a single financial effect:

![Duplicate callback deduplicated](docs/images/04-duplicate-callback.png)

**Compensating release.** When a provider declines, the reservation is released back to the customer with a compensating transfer, and the payment ends `failed` with the customer made whole. This is the live log of exactly that: `reserving -> funds_reserved -> provider_pending -> releasing -> failed`, with the release transfer posted to the ledger in between.

**Circuit breaker and fallback.** Repeated definitive unavailability from a provider opens its breaker and new payments route to a healthy provider. The log below shows both a compensating release and traffic that failed over from the primary provider, with the live `POST /auth/token` and `POST /transactions` calls to the ledger visible:

![Cloud Run logs](docs/images/08-cloud-run-logs.png)

**Reconcile guard.** Reconciliation is legal only for a payment left `UNKNOWN` by a timeout. Called on any other payment it is refused with a `409`, so the state machine is enforced rather than advisory:

![Reconcile guard](docs/images/05-reconcile-guard.png)

An ambiguous timeout is handled differently from a definitive failure: it pins the payment to its provider and reconciles, and never falls back, because the first provider may already have moved the money. That path, along with ledger-unavailability retry, is covered in the test suite below and detailed in [`DESIGN.md`](docs/DESIGN.md).

---

## Event delivery

Every state change is published as a fact so the reserve, capture and release lifecycle is visible to consumers rather than hidden inside a single "settled". Each event is written to an outbox table in the same transaction as the state change it describes, so an event exists if and only if that change committed. The relay wraps every row in the full ABS event envelope (`event_id`, `event_type`, `event_version`, `occurred_at`, `producer`, `correlation_id`, `causation_id`, `aggregate_id`, `payload`) and publishes to the `payment-events` Pub/Sub topic.

![Pub/Sub topic and subscription](docs/images/09-pubsub.png)

A single payment produces its whole causal chain, every event carrying the same `correlation_id`, published live and pulled straight back from the subscription:

```
EVENT_TYPE                  CORRELATION_ID
payment.received            bd7e9938-1005-42f7-b55a-c3c8755e7694
payment.approved            bd7e9938-1005-42f7-b55a-c3c8755e7694
payment.reserved            bd7e9938-1005-42f7-b55a-c3c8755e7694
payment.provider_succeeded  bd7e9938-1005-42f7-b55a-c3c8755e7694
payment.captured            bd7e9938-1005-42f7-b55a-c3c8755e7694
payment.settled             bd7e9938-1005-42f7-b55a-c3c8755e7694
```

![Pub/Sub delivery metrics](docs/images/09b-pubsub-metrics.png)

The guarantee is at-least-once, stated honestly. A row is marked published only after the transport accepts it, so a publish failure leaves it pending for retry, and consumers deduplicate on `event_id`. Publishing stops at the first failure so ordering holds and the failed row and everything after it stay pending.

---

## Architecture

```mermaid
flowchart TD
    C[Client] -->|HTTPS| A[FastAPI: payments, reconcile, callback, outbox]
    A --> S[Service layer: state machine, risk, reserve/capture/release]
    S --> DB[(PostgreSQL 16 / Cloud SQL)]
    DB -->|same transaction| OB[outbox_events]
    OB --> R[Relay: POST /outbox/publish]
    R --> PS[Pub/Sub topic payment-events]
    PS --> K[Idempotent consumers]

    S -->|reserve / capture / release| L[Ledger API]
    S -->|submit / reconcile| P[Providers: NorthPay, RapidPay, LegacyPay]

    subgraph GCP [Google Cloud, europe-west2]
        A
        S
        DB
        OB
        R
        PS
    end
```

FastAPI and Pydantic handle validation, routing and the OpenAPI spec. SQLAlchemy and Alembic own the schema and migrations. The orchestrator keeps its own state (`payments`, `payment_events`, `provider_attempts`, `outbox_events`) and never touches the ledger's database; it moves money only through the ledger API. It takes its own `payments` database and `orchestrator` user on the ledger's shared Cloud SQL instance rather than standing up a second server.

![Cloud Run service](docs/images/07-cloud-run.png)

The full GCP stack (the database and user, Artifact Registry, both secrets, a least-privilege runner service account, the Pub/Sub topic, subscription and publisher grant, and the Cloud Run service) is defined as code in [`terraform/`](terraform/), referencing the ledger's Cloud SQL instance through a data source so the dependency is explicit.

**Deployment.** GitHub Actions lints, runs the full test suite against a PostgreSQL service container, and validates the Terraform. On green it builds the image, pushes to Artifact Registry and deploys to Cloud Run. The database connection string and the ledger credential are injected from Secret Manager, never set as plaintext. Alembic migrations run automatically on container start.

| Its own database | Its own user | Secrets injected, not plaintext |
|---|---|---|
| ![Cloud SQL databases](docs/images/10-cloudsql-databases.png) | ![Cloud SQL users](docs/images/11-cloudsql-users.png) | ![Secret Manager](docs/images/13-secret-manager.png) |

![Artifact Registry](docs/images/12-artifact-registry.png)

---

## Verification and evidence

63 automated tests cover the state machine, the idempotent ledger client, the full service lifecycle, the provider router and circuit breaker, the transactional outbox, the API, and schema integrity. On top of the unit tests, the full path was exercised against the live deployment: a real payment settling against the live ledger (customer 1000.00 to 750.00, Payment Suspense netting to zero, Settlement Clearing 0 to 250.00), the six lifecycle events published to and pulled back from Pub/Sub, and each failure and edge path driven live and shown above.

**A defect the live deployment found.** The first live payment returned `500`. The `paymentstate` type is created by the migration from the enum's lowercase values (`received`), but the ORM defaulted to persisting the member names (`RECEIVED`), which the live type rejected. It passed CI because the suite builds tables from the model metadata, which is self-consistent, while the live schema is built by Alembic, so only production exercised the two against each other. The fix persists the enum values, and a schema test now fails if the model and the migration ever diverge on the enum labels again. Full write-up in [`PRODUCTION_LOG.md`](docs/PRODUCTION_LOG.md), Milestone 6.

**Requirement to evidence:**

| Requirement | How it is verified |
|-------------|--------------------|
| Provider never called before funds reserved (ABS-REQ-011) | `test_insufficient_funds_fails_and_never_reaches_provider`, live |
| At-most-once ledger effect, deterministic keys (ABS-REQ-002, 005) | `test_keys_are_deterministic`, `test_reserve_builds_transfer_customer_to_suspense`, capture and release equivalents |
| Ambiguous timeout pins and reconciles, never falls back (ABS-REQ-012) | `test_timeout_pins_to_provider_and_does_not_fall_back` |
| Reconcile resolves an UNKNOWN payment (ABS-REQ-004) | `test_reconcile_unknown_success_settles`, `test_reconcile_unknown_failure_fails` |
| Duplicate callback deduplicated (ABS-REQ-003) | `test_duplicate_callback_captures_once`, live |
| Definitive unavailability falls back; breaker opens | `test_unavailable_falls_back_to_next_provider`, `test_all_providers_unavailable_releases_reservation`, `test_breaker_opens_after_threshold` |
| Ledger unavailability leaves payment retryable | `test_ledger_unavailable_leaves_payment_reserving` |
| Compensating release on decline | `test_provider_failure_releases_and_fails`, live |
| Only legal state transitions occur | `test_legal_transitions_allowed`, `test_illegal_transitions_rejected` |
| Event capture atomic with state change, at-least-once (ABS-REQ-007) | `test_failed_publish_leaves_everything_pending`, `test_event_id_is_stable_for_consumer_dedup`, live Pub/Sub pull |
| Model and migration agree on schema | `test_orm_enum_matches_initial_migration` |

The full mapping of every requirement and behaviour to its test and live evidence is in [`docs/VV_PLAN.md`](docs/VV_PLAN.md).

---

## Running it locally

Requires Docker and Python 3.12.

```bash
# 1. start PostgreSQL
docker compose up -d

# 2. install dependencies
pip install -r requirements.txt

# 3. point the app at the database and apply migrations
export DATABASE_URL=postgresql://payments:payments@localhost:5433/payments
alembic upgrade head

# 4. run the service
uvicorn app.main:app --reload
```

Then open http://localhost:8000/docs for Swagger. With no `PUBSUB_TOPIC` set, the relay publishes to the structured log instead of Pub/Sub, so the full event path runs without cloud credentials. Point `LEDGER_BASE_URL` at a running [ledger](https://github.com/abdullahabduljabbarab/ledger-api) to settle real transactions.

**Running the tests** (the suite creates its own test database):

```bash
export TEST_DATABASE_URL=postgresql://payments:payments@localhost:5433/payments_test
pytest
```

---

## Design decisions

**Reserve, capture, release, not a single write.** Two-phase movement through Payment Suspense and Settlement Clearing gives every stage an explicit financial meaning, so a failure is a compensating transfer rather than a state that should not exist.

**At-most-once ledger effect per operation, not exactly-once delivery.** No broker or provider protocol gives exactly-once delivery, and a payment deliberately makes more than one movement. So the claim is made per operation: each reserve, capture and release is keyed deterministically and re-issued until the ledger's answer is recorded, which duplicates and crashes cannot break.

**Timeout is not failure.** A definitive failure may fall back to another provider; an ambiguous timeout pins the payment to its provider and reconciles, because failing over could move the same money twice.

**Its own database on the shared instance.** The orchestrator keeps its own schema and user on the ledger's Cloud SQL instance and settles only through the ledger API, so financial state stays owned by the ledger while the orchestrator owns its own lifecycle state.

The full design, including the complete state machine and the provider model, is in [`DESIGN.md`](docs/DESIGN.md), and the build history is in [`PRODUCTION_LOG.md`](docs/PRODUCTION_LOG.md).

---

## Project layout

```
app/            FastAPI application, state machine, service layer, ledger client, providers, router, outbox relay
migrations/     Alembic migrations (schema and enum, then the ABS event envelope)
terraform/      Orchestrator infrastructure as code on the ledger's project
tests/          63 tests: state machine, ledger client, service lifecycle, router, outbox, API, schema
docs/           Design, the V&V plan, the build log, and the evidence screenshots
```

---

Built by Abdullah Ameed Abduljabbar.
