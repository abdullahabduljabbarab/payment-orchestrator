# Architecture Decision Records

## ADR-001: Reserve, capture, release over a single ledger write

A payment is modelled as a two-phase movement of money through three positions (customer, Payment Suspense, Settlement Clearing) rather than a single transfer. Each stage has an explicit financial meaning, and no stage can leave money in an impossible place. The alternative, a single transfer on confirmed success, has no correct answer for the window between asking a provider to move money and learning whether it did. The two-phase model gives that window a name (FUNDS_RESERVED, PROVIDER_PENDING, UNKNOWN) and a defined resolution.

## ADR-002: At-most-once ledger effect per operation, not exactly-once delivery

No broker or provider protocol delivers exactly-once, and a single payment deliberately makes more than one financial movement. So the guarantee is stated per operation rather than per payment: each reserve, capture and release has an at-most-once ledger effect and is retried to completion. Claiming exactly-once delivery would be a claim the system cannot honour.

## ADR-003: Ambiguous timeout pins and reconciles; only definitive failure falls back

A definitive failure (a provider that refused or was unreachable before accepting the request) may fall back to another provider. An ambiguous timeout, where the request may already have reached the provider, moves the payment to UNKNOWN and pins it to that provider until reconciliation. Falling over from a timeout could route the same payment to a second provider and move the money twice. This is the single most important resilience decision in the service (ABS-REQ-012).

## ADR-004: Deterministic idempotency keys per operation

Each operation is keyed `payment:{id}:reserve|capture|release` and the key is passed to the ledger, which returns the original transaction for a repeated key. If the orchestrator crashes after the ledger commits but before recording the result, the retry returns the original transfer rather than making a second one. The financial effect is therefore idempotent at the ledger, not just guarded by the orchestrator's own state (ABS-REQ-002, 005).

## ADR-005: Transactional outbox with two real transports

Events are written to an `outbox_events` table in the same database transaction as the state change they describe, so an event exists if and only if that change committed. The relay publishes to Pub/Sub in the cloud and to the structured log locally, selected by the `PUBSUB_TOPIC` environment variable. Both are real transports, so local runs and tests exercise the identical relay path, including the failure handling, without cloud credentials. Skipping the publish and marking rows delivered anyway would make the endpoint lie about what it did.

## ADR-006: Own database and user on the ledger's shared Cloud SQL instance

The orchestrator keeps its own schema (`payments`, `payment_events`, `provider_attempts`, `outbox_events`) and its own database user, on the ledger's existing Cloud SQL instance rather than a second server. Financial state stays owned by the ledger, reached only through the ledger API, while the orchestrator owns its own lifecycle state. One instance, isolated schemas, a smaller surface, and the shared-instance dependency is expressed as a Terraform data source so the ledger is provisioned first.

## ADR-007: Risk as a threshold stub behind an explicit contract

The risk decision is a deterministic threshold (review at 5000, block at 10000) rather than a model. The engineering point is the contract, allow / review / block with reasons, and the state machine that consumes it. The real decision moves to a dedicated risk engine later; the orchestrator is already built to hand it off.

## ADR-008: A single state machine as the source of transition truth

Every state change goes through one guard that enforces the allowed-transition table, writes the append-only event, writes the outbox event, and commits, all in one transaction. An illegal move is a programming error caught immediately rather than a corrupt payment discovered later, and a payment is always left durable and legal, which makes crash recovery a matter of resuming from the state on disk.

## ADR-009: Persist the state enum by value, so the ORM and the migration agree

The `paymentstate` type is created by the migration from the enum's lowercase values, and the ORM is configured with `values_callable` to persist the same values rather than the member names. The two must agree or the database rejects every insert, which is exactly what happened on the first live deploy before this was fixed. A schema test now fails if the model and the migration ever diverge on the enum labels again.
