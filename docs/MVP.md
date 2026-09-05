# MVP Scope

## Objective

Build and deploy a cloud-hosted payment orchestrator that settles against a live double-entry ledger, demonstrating the engineering that keeps money consistent across the failure cases real payment systems hit.

## Must-have

- Accept a payment and run it through an explicit state machine
- Risk decision (allow, review, block) as a defined contract, before any money moves
- Reserve, capture and release settlement against the ledger, through Payment Suspense and Settlement Clearing
- Provider never contacted before funds are reserved
- Deterministic idempotency keys so each operation has an at-most-once ledger effect
- Ambiguous timeout handling: pin to provider, reconcile, never fall back
- Circuit breaker and fallback across three simulated providers
- Duplicate provider callback protection
- Append-only event history per payment
- Transactional outbox publishing to Pub/Sub with the ABS event envelope
- PostgreSQL (local and production), Alembic migrations
- Google Cloud Run deployment against a live ledger
- Terraform for all infrastructure
- CI/CD (GitHub Actions): lint, test, Terraform validate, deploy
- Automated test suite
- OpenAPI documentation (/docs)

## Not in scope

- A real risk engine (the risk decision is a threshold stub with the real contract; the engine is a separate ABS service)
- Real payment providers or rails (three simulators with distinct failure personalities stand in)
- Authentication on the orchestrator's own endpoints
- A continuously running outbox worker (the relay is an endpoint, callable on a schedule)
- Frontend
- Real personal or financial data

## Definition of Done

- Live Cloud Run endpoint returning 200 on /health
- A real payment settling end to end against the live ledger, reserve into Payment Suspense and capture into Settlement Clearing
- The failure and edge paths shown against the live deployment
- Events published to and pulled back from Pub/Sub
- All tests green against PostgreSQL, CI/CD deploying on green main, Terraform validating
- /docs (Swagger) accessible on the live endpoint
- README complete with architecture, the financial model, and live evidence
