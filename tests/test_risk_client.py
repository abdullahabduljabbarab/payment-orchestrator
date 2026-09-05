"""The risk client maps the engine's responses, and fails to unavailable."""

import uuid
from decimal import Decimal

import httpx
import pytest

from app.risk_client import HttpRiskClient, RiskUnavailable, evaluation_id_for


def _client(handler):
    client = HttpRiskClient("http://risk")
    client._client = httpx.Client(transport=httpx.MockTransport(handler))
    return client


def _args():
    return dict(
        evaluation_id=uuid.uuid4(),
        payment_id=uuid.uuid4(),
        account_id=uuid.uuid4(),
        amount=Decimal("100.00"),
        destination="acme",
        correlation_id=uuid.uuid4(),
    )


def test_success_maps_to_result():
    def handler(request):
        return httpx.Response(
            200,
            json={"decision": "review", "score": 63, "reasons": [{"rule": "HIGH_VALUE"}]},
        )

    result = _client(handler).evaluate(**_args())
    assert result.decision == "review"
    assert result.score == 63


def test_server_error_maps_to_unavailable():
    with pytest.raises(RiskUnavailable):
        _client(lambda r: httpx.Response(503)).evaluate(**_args())


def test_connection_error_maps_to_unavailable():
    def handler(request):
        raise httpx.ConnectError("down")

    with pytest.raises(RiskUnavailable):
        _client(handler).evaluate(**_args())


def test_evaluation_id_is_deterministic_per_payment():
    payment_id = uuid.uuid4()
    assert evaluation_id_for(payment_id) == evaluation_id_for(payment_id)
    assert evaluation_id_for(payment_id) != evaluation_id_for(uuid.uuid4())
