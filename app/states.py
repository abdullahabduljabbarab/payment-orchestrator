"""The payment state machine.

The allowed transitions are the single source of truth for how a payment may
move. Nothing advances a payment except through `assert_transition`, so an
illegal move is a programming error caught immediately rather than a corrupt
payment discovered later.
"""

import enum


class PaymentState(str, enum.Enum):
    RECEIVED = "received"
    RISK_PENDING = "risk_pending"
    RISK_REVIEW = "risk_review"
    REJECTED = "rejected"
    APPROVED = "approved"
    RESERVING = "reserving"
    FUNDS_RESERVED = "funds_reserved"
    PROVIDER_PENDING = "provider_pending"
    CAPTURING = "capturing"
    RELEASING = "releasing"
    UNKNOWN = "unknown"
    SETTLED = "settled"
    FAILED = "failed"


TERMINAL_STATES: frozenset[PaymentState] = frozenset(
    {PaymentState.REJECTED, PaymentState.SETTLED, PaymentState.FAILED}
)

# Every legal move. A state maps to the set of states it may transition to.
ALLOWED_TRANSITIONS: dict[PaymentState, frozenset[PaymentState]] = {
    PaymentState.RECEIVED: frozenset({PaymentState.RISK_PENDING}),
    PaymentState.RISK_PENDING: frozenset(
        {PaymentState.APPROVED, PaymentState.REJECTED, PaymentState.RISK_REVIEW}
    ),
    PaymentState.RISK_REVIEW: frozenset(
        {PaymentState.APPROVED, PaymentState.REJECTED}
    ),
    PaymentState.APPROVED: frozenset({PaymentState.RESERVING}),
    PaymentState.RESERVING: frozenset(
        {PaymentState.FUNDS_RESERVED, PaymentState.FAILED}
    ),
    PaymentState.FUNDS_RESERVED: frozenset({PaymentState.PROVIDER_PENDING}),
    PaymentState.PROVIDER_PENDING: frozenset(
        {PaymentState.CAPTURING, PaymentState.RELEASING, PaymentState.UNKNOWN}
    ),
    PaymentState.UNKNOWN: frozenset(
        {PaymentState.CAPTURING, PaymentState.RELEASING}
    ),
    PaymentState.CAPTURING: frozenset({PaymentState.SETTLED}),
    PaymentState.RELEASING: frozenset({PaymentState.FAILED}),
    PaymentState.REJECTED: frozenset(),
    PaymentState.SETTLED: frozenset(),
    PaymentState.FAILED: frozenset(),
}


class IllegalTransition(Exception):
    def __init__(self, current: PaymentState, target: PaymentState):
        self.current = current
        self.target = target
        super().__init__(f"illegal transition {current.value} -> {target.value}")


def can_transition(current: PaymentState, target: PaymentState) -> bool:
    return target in ALLOWED_TRANSITIONS[current]


def assert_transition(current: PaymentState, target: PaymentState) -> None:
    if not can_transition(current, target):
        raise IllegalTransition(current, target)


def is_terminal(state: PaymentState) -> bool:
    return state in TERMINAL_STATES
