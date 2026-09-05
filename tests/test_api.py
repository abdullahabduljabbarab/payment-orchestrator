from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.database import get_db
from app.main import app, get_ledger_client, get_provider_router
from app.providers import Outcome, ScriptedProvider
from app.router import ProviderRouter
from tests.fakes import FakeLedgerClient


@pytest.fixture
def client(db):
    def override_db():
        yield db

    fake = FakeLedgerClient()
    router = ProviderRouter([ScriptedProvider("P1", [Outcome.SUCCESS])])
    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_ledger_client] = lambda: fake
    app.dependency_overrides[get_provider_router] = lambda: router
    with TestClient(app) as c:
        yield c, fake
    app.dependency_overrides.clear()


def test_post_payment_settles(client):
    c, _ = client
    resp = c.post(
        "/payments",
        json={"account_id": str(uuid4()), "amount": "100.00", "destination": "acme"},
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["state"] == "settled"
    assert body["reserve_tx_id"] is not None
    assert body["capture_tx_id"] is not None

    pid = body["id"]
    events = c.get(f"/payments/{pid}/events").json()
    to_states = [e["to_state"] for e in events]
    assert to_states[0] == "received"
    assert to_states[-1] == "settled"


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


def test_reconcile_on_settled_payment_conflicts(client):
    c, _ = client
    resp = c.post(
        "/payments",
        json={"account_id": str(uuid4()), "amount": "100.00", "destination": "acme"},
    )
    pid = resp.json()["id"]
    # already settled, so it is not awaiting reconciliation
    assert c.post(f"/payments/{pid}/reconcile").status_code == 409
