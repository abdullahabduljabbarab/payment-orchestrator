import json
from decimal import Decimal
from uuid import UUID, uuid4

import httpx
import pytest

from app.ledger_client import (
    HttpLedgerClient,
    InsufficientFunds,
    LedgerUnavailable,
    capture_key,
    release_key,
    reserve_key,
)

SUSPENSE = uuid4()
SETTLEMENT = uuid4()


def make_client(handler):
    client = HttpLedgerClient(
        base_url="http://ledger",
        username="u",
        password="p",
        suspense_account_id=SUSPENSE,
        settlement_account_id=SETTLEMENT,
    )
    client._client = httpx.Client(transport=httpx.MockTransport(handler))
    client._token = "test-token"  # skip auth in the unit test
    return client


def test_keys_are_deterministic():
    pid = uuid4()
    assert reserve_key(pid) == f"payment:{pid}:reserve"
    assert capture_key(pid) == f"payment:{pid}:capture"
    assert release_key(pid) == f"payment:{pid}:release"


def test_reserve_builds_transfer_customer_to_suspense():
    pid = uuid4()
    account = uuid4()
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(201, json={"id": str(uuid4())})

    client = make_client(handler)
    tx = client.reserve(pid, account, Decimal("500.00"))

    assert isinstance(tx, UUID)
    body = captured["body"]
    assert body["type"] == "transfer"
    assert body["amount"] == "500.00"
    assert body["from_account_id"] == str(account)
    assert body["to_account_id"] == str(SUSPENSE)
    assert body["idempotency_key"] == reserve_key(pid)


def test_capture_moves_suspense_to_settlement():
    pid = uuid4()
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(201, json={"id": str(uuid4())})

    client = make_client(handler)
    client.capture(pid, Decimal("500.00"))

    body = captured["body"]
    assert body["from_account_id"] == str(SUSPENSE)
    assert body["to_account_id"] == str(SETTLEMENT)
    assert body["idempotency_key"] == capture_key(pid)


def test_release_returns_funds_to_customer():
    pid = uuid4()
    account = uuid4()
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(201, json={"id": str(uuid4())})

    client = make_client(handler)
    client.release(pid, account, Decimal("500.00"))

    body = captured["body"]
    assert body["from_account_id"] == str(SUSPENSE)
    assert body["to_account_id"] == str(account)
    assert body["idempotency_key"] == release_key(pid)


def test_insufficient_balance_maps_to_insufficient_funds():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(422, json={"detail": "Insufficient balance"})

    client = make_client(handler)
    with pytest.raises(InsufficientFunds):
        client.reserve(uuid4(), uuid4(), Decimal("500.00"))


def test_server_error_maps_to_unavailable():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="down")

    client = make_client(handler)
    with pytest.raises(LedgerUnavailable):
        client.reserve(uuid4(), uuid4(), Decimal("500.00"))
