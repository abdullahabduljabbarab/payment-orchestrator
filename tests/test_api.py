from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.database import get_db
from app.main import app, get_ledger_client
from tests.fakes import FakeLedgerClient


@pytest.fixture
def client(db):
    def override_db():
        yield db

    fake = FakeLedgerClient()
    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_ledger_client] = lambda: fake
    with TestClient(app) as c:
        yield c, fake
    app.dependency_overrides.clear()


def test_post_payment_reserves(client):
    c, _ = client
    resp = c.post(
        "/payments",
        json={"account_id": str(uuid4()), "amount": "100.00", "destination": "acme"},
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["state"] == "funds_reserved"
    assert body["reserve_tx_id"] is not None

    pid = body["id"]
    assert c.get(f"/payments/{pid}").status_code == 200

    events = c.get(f"/payments/{pid}/events").json()
    to_states = [e["to_state"] for e in events]
    assert to_states[0] == "received"
    assert "funds_reserved" in to_states


def test_post_payment_over_limit_rejected(client):
    c, fake = client
    resp = c.post(
        "/payments",
        json={"account_id": str(uuid4()), "amount": "10000.00", "destination": "acme"},
    )
    assert resp.status_code == 201
    assert resp.json()["state"] == "rejected"
    assert fake.calls == []


def test_invalid_amount_rejected(client):
    c, _ = client
    resp = c.post(
        "/payments",
        json={"account_id": str(uuid4()), "amount": "-5.00", "destination": "acme"},
    )
    assert resp.status_code == 422


def test_get_missing_payment_404(client):
    c, _ = client
    assert c.get(f"/payments/{uuid4()}").status_code == 404
