# Requirements

The orchestrator owns a subset of the ABS system requirements, defined in [SYSTEM_REQUIREMENTS.md](https://github.com/abdullahabduljabbarab/abs-financial-systems/blob/main/SYSTEM_REQUIREMENTS.md). Those it owns are referenced against the functional and non-functional requirements below.

## Functional

| ID | Requirement | ABS |
|----|-------------|-----|
| REQ-F-001 | The system shall accept a payment request (account, amount, destination), assign it a durable identity, and place it in a RECEIVED state. | |
| REQ-F-002 | The system shall submit every payment to a risk decision before reserving funds, yielding allow, review or block. | |
| REQ-F-003 | The system shall reserve funds by transferring from the customer account to Payment Suspense after approval and before contacting any provider. | ABS-REQ-011 |
| REQ-F-004 | The system shall never contact a provider for a payment whose reservation did not succeed. | ABS-REQ-011 |
| REQ-F-005 | The system shall capture a confirmed payment by transferring from Payment Suspense to Settlement Clearing. | |
| REQ-F-006 | The system shall release a failed payment by transferring from Payment Suspense back to the customer as a compensating entry, never a deletion. | |
| REQ-F-007 | The system shall key each reserve, capture and release with a deterministic idempotency key so a retry returns the original ledger transaction. | ABS-REQ-005 |
| REQ-F-008 | The system shall hold a payment the risk decision flags for review, without reserving funds or contacting a provider. | |
| REQ-F-009 | The system shall reject a payment the risk decision blocks. | |
| REQ-F-010 | The system shall move a payment to UNKNOWN on an ambiguous provider timeout, hold the reservation, and pin the payment to that provider. | ABS-REQ-012 |
| REQ-F-011 | The system shall resolve an UNKNOWN payment only by reconciling with the same provider, then capturing or releasing. | ABS-REQ-004 |
| REQ-F-012 | The system shall deduplicate provider callbacks on `(provider, provider_reference)` so a repeated callback produces a single financial effect. | ABS-REQ-003 |
| REQ-F-013 | The system shall record every state transition in an append-only log so a payment's history is fully reconstructable. | |
| REQ-F-014 | The system shall emit a domain event for every meaningful transition through a transactional outbox. | ABS-REQ-007 |
| REQ-F-015 | The system shall expose a health endpoint that probes the database. | |

## Non-Functional

| ID | Requirement | ABS |
|----|-------------|-----|
| REQ-NF-001 | All monetary values shall use Decimal (Python) and Numeric(12,2) (PostgreSQL). No floats. | |
| REQ-NF-002 | Each payment operation shall have an at-most-once ledger effect under retries, crashes and duplicate callbacks. | ABS-REQ-002 |
| REQ-NF-003 | Events shall be delivered at least once, captured atomically with the state change they describe, and deduplicated by consumers on `event_id`. | ABS-REQ-008, 009 |
| REQ-NF-004 | Only a definitive provider failure shall trigger fallback to another provider; an ambiguous timeout shall never fall back. | ABS-REQ-012 |
| REQ-NF-005 | The database connection string and the ledger credential shall be stored in Secret Manager and injected at runtime, never in source control. | |
| REQ-NF-006 | The CI pipeline shall lint, run the full test suite against PostgreSQL, and validate the Terraform before deploy. | |
| REQ-NF-007 | Local development shall use PostgreSQL via Docker Compose, matching the production database engine. | |
| REQ-NF-008 | Schema changes shall be managed through versioned Alembic migrations. | |
| REQ-NF-009 | The orchestrator shall not read or write the ledger's database directly; all money movement shall go through the ledger API. | |
| REQ-NF-010 | A payment shall always be left in a durable, legal state, so crash recovery is a matter of resuming from the state on disk. | |
