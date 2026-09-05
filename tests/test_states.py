import pytest

from app.states import (
    IllegalTransition,
    PaymentState,
    assert_transition,
    can_transition,
    is_terminal,
)

LEGAL = [
    (PaymentState.RECEIVED, PaymentState.RISK_PENDING),
    (PaymentState.RISK_PENDING, PaymentState.APPROVED),
    (PaymentState.RISK_PENDING, PaymentState.REJECTED),
    (PaymentState.RISK_PENDING, PaymentState.RISK_REVIEW),
    (PaymentState.RISK_REVIEW, PaymentState.APPROVED),
    (PaymentState.RISK_REVIEW, PaymentState.REJECTED),
    (PaymentState.APPROVED, PaymentState.RESERVING),
    (PaymentState.RESERVING, PaymentState.FUNDS_RESERVED),
    (PaymentState.RESERVING, PaymentState.FAILED),
    (PaymentState.FUNDS_RESERVED, PaymentState.PROVIDER_PENDING),
    (PaymentState.PROVIDER_PENDING, PaymentState.CAPTURING),
    (PaymentState.PROVIDER_PENDING, PaymentState.RELEASING),
    (PaymentState.PROVIDER_PENDING, PaymentState.UNKNOWN),
    (PaymentState.UNKNOWN, PaymentState.CAPTURING),
    (PaymentState.UNKNOWN, PaymentState.RELEASING),
    (PaymentState.CAPTURING, PaymentState.SETTLED),
    (PaymentState.RELEASING, PaymentState.FAILED),
]

ILLEGAL = [
    # risk cannot be skipped
    (PaymentState.RECEIVED, PaymentState.APPROVED),
    # the provider is never reached before funds are reserved (ABS-REQ-011)
    (PaymentState.APPROVED, PaymentState.PROVIDER_PENDING),
    (PaymentState.RESERVING, PaymentState.PROVIDER_PENDING),
    # a reservation must exist before the provider call
    (PaymentState.FUNDS_RESERVED, PaymentState.CAPTURING),
    # settlement only follows a capture
    (PaymentState.PROVIDER_PENDING, PaymentState.SETTLED),
]


@pytest.mark.parametrize("current,target", LEGAL)
def test_legal_transitions_allowed(current, target):
    assert can_transition(current, target)
    assert_transition(current, target)


@pytest.mark.parametrize("current,target", ILLEGAL)
def test_illegal_transitions_rejected(current, target):
    assert not can_transition(current, target)
    with pytest.raises(IllegalTransition):
        assert_transition(current, target)


def test_terminal_states_have_no_exit():
    for terminal in (PaymentState.SETTLED, PaymentState.FAILED, PaymentState.REJECTED):
        assert is_terminal(terminal)
        for target in PaymentState:
            assert not can_transition(terminal, target)


def test_transient_states_are_not_terminal():
    for state in (
        PaymentState.RECEIVED,
        PaymentState.FUNDS_RESERVED,
        PaymentState.PROVIDER_PENDING,
        PaymentState.UNKNOWN,
    ):
        assert not is_terminal(state)
