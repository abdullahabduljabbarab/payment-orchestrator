# Security

## What this project is

A portfolio demonstration of payment orchestration and cloud engineering. It is not a production payment system, uses simulated providers, and handles no real personal or financial data.

## Boundaries

**Secrets management.** The database connection string and the ledger credential are held in GCP Secret Manager, provisioned via Terraform, and injected into Cloud Run at runtime. They are never set as plaintext environment variables and never committed to source control. `.gitignore` excludes `.env` and Terraform state.

**No direct access to financial state.** The orchestrator never reads or writes the ledger's database. All money movement goes through the ledger API as authenticated reserve, capture and release transfers, so the ledger remains the sole authority over financial records and keeps its own tamper-evident guarantees.

**Input validation.** All requests are validated through Pydantic models before reaching the service layer. Invalid types, negative or zero amounts, missing fields and malformed UUIDs are rejected with structured error responses.

**SQL injection.** All database access goes through SQLAlchemy's ORM with parameterised queries. No raw string interpolation in SQL.

**Idempotency and replay.** Provider callbacks are deduplicated on `(provider, provider_reference)` with a unique constraint, and a callback only acts on a payment in PROVIDER_PENDING or UNKNOWN. A replayed or late callback is recorded and ignored, so it cannot drive a second capture or release.

**Identifiers.** Payments, events and accounts use UUIDs. No sequential integer IDs are exposed.

**Error responses.** Structured JSON errors. No stack traces or internal state leaked in responses.

**HTTPS.** Terminated by Cloud Run at the infrastructure level. The application does not handle TLS.

## Authentication

The orchestrator authenticates outward to the ledger with a credential drawn from Secret Manager. Its own endpoints are unauthenticated, which is a deliberate scope decision for a portfolio project, the same decision the ledger makes. Adding token auth on the orchestrator, and signed-webhook verification on the provider callback, would be straightforward and is noted below rather than built.

## Known limitations

- No authentication on the orchestrator's own endpoints, and no signature verification on the provider callback. In production the callback would verify a provider signature so a forged callback could not be accepted at all, rather than relying on the state and dedup guards to bound its effect.
- The ledger credential is an admin credential. In production this would be a scoped service identity that can post transfers but cannot provision accounts or read audit data.
- No rate limiting. Would be added via Cloud Armor or middleware.
- No audit logging beyond the append-only payment event history, the provider-attempt records, and structured JSON logs.
- Encryption at rest relies on the managed encryption provided by Cloud SQL. Customer-managed keys are outside project scope.
