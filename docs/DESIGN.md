# Payment Orchestrator: Design

## Purpose

The orchestrator manages everything between a customer requesting a payment and that payment being settled in the ledger. It is the only service that talks to external payment providers, and the only service that asks the ledger to move a customer's money. Risk decides whether a payment may proceed; the ledger records every money movement; the orchestrator owns the lifecycle that connects them and survives the failures in between.

The whole reason this service exists is the space between "a provider was asked to move money" and "we know whether it did." That is where real payment systems break. A timeout is not a failure. A duplicate callback is not a second payment. A ledger that is briefly unavailable is not a lost settlement. And money must never leave a customer's account for a provider that then declines, nor be sent to a provider before we know the customer can cover it. The orchestrator is built around those cases, not around the happy path.

## The financial model: reserve, capture, release

A payment is not a single ledger write. It is a two-phase movement of money through three positions, so that every stage has an explicit financial meaning and no stage can leave money in an impossible place.

```
   Customer Account
        |
        |  reserve      (before any provider call)
        v
   Payment Suspense
        |
        |  capture      (on provider success)
        v
   Settlement Clearing
```

The failure path is a compensating movement, not an undo. Because the ledger is append-only, releasing a reservation does not delete it; it adds an equal and opposite transfer, and both remain permanently visible in history.

```
   Customer Account                 Customer Account
        |                                 ^
        |  reserve £500                   |  release £500
        v                                 |
   Payment Suspense  --- provider FAILED ---
```

Payment Suspense and Settlement Clearing are system accounts in the ledger, alongside the existing External Clearing account. Each of the three operations is an ordinary ledger transfer:

| Operation | Ledger transfer | When |
|-----------|-----------------|------|
| reserve | Customer to Payment Suspense | after approval, before any provider call |
| capture | Payment Suspense to Settlement Clearing | on confirmed provider success |
| release | Payment Suspense to Customer | on confirmed provider failure |

The ledger checks that the source account has the funds, so reserve is where an insufficient balance is caught. Capture and release move funds that are already held in Suspense, so they cannot fail for lack of funds.

The crucial ordering: **the provider is never called unless the reservation succeeded.** That removes the worst inconsistency in payments, where a provider moves money and the ledger then finds the customer cannot cover it.

## The state machine

A payment is a state machine. Every transition is triggered by a specific event and is the only way to move between states.

```
        RECEIVED
           |
           v
      RISK_PENDING
       |    |     |
    block review allow
       |    |     |
       v    v     |
  REJECTED RISK_REVIEW
            |     |    |
         reject approve|
            |     |    |
            v     v    v
        REJECTED   APPROVED
                      |
                      v
                  RESERVING
              |     |
       insufficient reserved
              |     |
              v     v
           FAILED  FUNDS_RESERVED
                        |
                        v
                  PROVIDER_PENDING
                   |     |      |
                success failed timeout
                   |     |      |
                   v     v      v
              CAPTURING RELEASING UNKNOWN
                   |     |         |
                   |     |     reconcile
                   |     |      |       |
                   |     |   success  failed
                   |     |      |       |
                   v     v      v       v
                SETTLED FAILED CAPTURING RELEASING
                                 |         |
                                 v         v
                             SETTLED    FAILED
```

| From | Event | To |
|------|-------|-----|
| (start) | payment request accepted | RECEIVED |
| RECEIVED | submitted to risk | RISK_PENDING |
| RISK_PENDING | risk allows | APPROVED |
| RISK_PENDING | risk blocks | REJECTED (terminal) |
| RISK_PENDING | risk flags for review | RISK_REVIEW |
| RISK_REVIEW | review approves | APPROVED |
| RISK_REVIEW | review rejects | REJECTED (terminal) |
| APPROVED | reservation started | RESERVING |
| RESERVING | funds reserved | FUNDS_RESERVED |
| RESERVING | insufficient funds | FAILED (terminal), provider never called |
| FUNDS_RESERVED | sent to provider | PROVIDER_PENDING |
| PROVIDER_PENDING | provider confirms success | CAPTURING |
| PROVIDER_PENDING | provider confirms failure | RELEASING |
| PROVIDER_PENDING | provider times out | UNKNOWN |
| UNKNOWN | reconciliation finds success | CAPTURING |
| UNKNOWN | reconciliation finds failure | RELEASING |
| CAPTURING | capture committed in ledger | SETTLED (terminal) |
| RELEASING | release committed in ledger | FAILED (terminal) |

Terminal states are REJECTED, FAILED and SETTLED. Every transient state has a defined way out, including UNKNOWN, which is the one state that does not resolve itself and holds the reservation until reconciliation decides to capture or release it.

RISK_REVIEW is a held state for payments the risk engine neither allows nor blocks outright. It is resolved by a review decision that approves or rejects the payment. In v1 that decision comes from an operator endpoint; a later version can drive it from an automated review policy. No reservation is made and no provider is contacted while a payment sits in RISK_REVIEW.

## Providers

Three simulated external providers, each with a different failure personality, so the orchestrator's resilience is exercised rather than asserted.

| Provider | Behaviour |
|----------|-----------|
| NorthPay | Cheap, usually fast, occasionally unavailable (503s in bursts). Exercises the circuit breaker and fallback routing. |
| RapidPay | More reliable, more expensive. The fallback when NorthPay's breaker is open. |
| LegacyPay | Slow, and sometimes sends the same callback more than once. Exercises timeout handling and duplicate-callback protection. |

The orchestrator routes to a provider, and on repeated failure from one it opens a circuit breaker and routes elsewhere.

## Timeouts and UNKNOWN

When a provider request times out, the orchestrator does not know whether the provider committed the payment. Treating a timeout as failure risks releasing a reservation for a payment the provider actually made. Treating it as success risks capturing for a payment the provider never made. Retrying blindly risks doing it twice.

So a timeout moves the payment to **UNKNOWN**, the reservation stays held, and UNKNOWN is resolved by asking the provider directly (reconciliation) before capturing or releasing. Only once the provider has confirmed does the payment leave UNKNOWN. This is [ABS-REQ-004](https://github.com/abdullahabduljabbarab/abs-financial-systems/blob/main/SYSTEM_REQUIREMENTS.md).

This draws a hard line between a definitive failure and an ambiguous one. **Fallback to another provider is permitted only after a definitive failure**: a 503 before the provider accepted the request, a refused connection, a provider that is known to be down. An **ambiguous timeout, where the request may already have reached the provider, pins the payment to that provider** until reconciliation resolves it. The orchestrator never fails over from a timeout, because the first provider may already have moved the money, and routing the same payment to a second provider would move it twice. This is [ABS-REQ-012](https://github.com/abdullahabduljabbarab/abs-financial-systems/blob/main/SYSTEM_REQUIREMENTS.md).

## The guarantee: at-most-once ledger effect per operation

The property this service provides is not exactly-once *delivery*. No broker or provider protocol gives that, and claiming it invites a fair challenge. A single payment also deliberately makes more than one financial movement: a reserve, then a capture or a release. So the claim is made per operation, not per payment:

**Each payment lifecycle operation has an at-most-once ledger effect with retry-to-completion.** Consequently, retries, crashes and duplicate callbacks cannot create duplicate reserve, capture or release movements. An operation is re-issued until the orchestrator has recorded the ledger's answer, so it also cannot be lost.

The mechanism is a deterministic idempotency key per financial operation:

```
payment:{payment_id}:reserve
payment:{payment_id}:capture
payment:{payment_id}:release
```

Each key is passed to the ledger, which returns the original transaction for a repeated key. So if the orchestrator crashes after the ledger commits but before its own database records the result:

- retry reserve returns the original reserve transfer,
- retry capture returns the original capture transfer,
- retry release returns the original compensating transfer.

No duplicated money movement, and no lost one, because the step is re-issued until the orchestrator has recorded the ledger's answer. This gives ABS-REQ-002 (a payment's financial effect happens at most once) and ABS-REQ-005 (a payment maps to a deterministic, idempotently keyed set of ledger transactions).

## Resilience

- **Reservation before provider.** A payment whose reservation fails is failed immediately and the provider is never contacted.
- **Retries with backoff.** A *definitive* transient error (a 503 before acceptance, a refused connection) is retried with exponential backoff and jitter, and after the limit the payment may fail over to another provider. An *ambiguous* timeout is never retried against a different provider; it goes to UNKNOWN and stays pinned to the original provider until reconciliation.
- **Circuit breaker.** Repeated definitive failures from one provider open its breaker; new payments route to a healthy provider while it is open, and it half-opens to test recovery. A payment already pinned to a provider by a timeout is not moved by the breaker.
- **Duplicate callback protection.** Provider callbacks are deduplicated on the pair `(provider, provider_reference)` with a unique database constraint, so LegacyPay sending the same success three times results in one capture ([ABS-REQ-003](https://github.com/abdullahabduljabbarab/abs-financial-systems/blob/main/SYSTEM_REQUIREMENTS.md)). Receipt of a duplicate is recorded and ignored, never reprocessed.
- **Ledger unavailability.** If the ledger is briefly unavailable during reserve, capture or release, the payment stays in that state and the operation is retried. The idempotency key makes every retry safe.

## Data model

- **payments**: id, account_id, amount, destination, current state, provider, `reserve_tx_id`, `capture_tx_id`, `release_tx_id`, correlation_id, timestamps.
- **payment_events**: an append-only log of every state transition, with the triggering event and timestamp, so a payment's history is fully reconstructable.
- **provider_attempts**: `provider`, `provider_reference`, `callback_type`, `outcome`, `received_at`, `processed_at`, with a unique constraint on `(provider, provider_reference)`. Two providers could independently generate the same reference string, so the provider is part of the key. The unique constraint is what makes a duplicate callback harmless: the second insert is rejected and the callback is ignored.

## Events emitted

The orchestrator emits a fact for each meaningful transition, so the reserve, capture and release lifecycle is visible to consumers rather than hidden inside a single "settled". Provider success is deliberately not the same event as the payment being settled, because a provider confirming success only leads to a capture; the payment is settled once the capture commits.

`payment.received`, `payment.approved`, `payment.rejected`, `payment.reserved`, `payment.reservation_failed`, `payment.provider_succeeded`, `payment.provider_failed`, `payment.unknown`, `payment.captured`, `payment.released`, `payment.settled`, `payment.failed`. Full payloads are in the [event catalogue](https://github.com/abdullahabduljabbarab/abs-financial-systems/blob/main/EVENT_CATALOGUE.md). Each is published through a transactional outbox so an event exists if and only if the state change committed.

## Requirements satisfied

ABS-REQ-002, 003, 004, 005, 011 and 012 are owned here. ABS-REQ-007, 008 and 009 apply to its events and consumers. Each is mapped to a named test as the code is built.
