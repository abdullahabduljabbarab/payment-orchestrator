"""
Load test harness for the Payment Orchestrator.

Run against the live GCP deployment:
    locust -f scripts/loadtest.py --host https://payment-orchestrator-eppidgbmxa-nw.a.run.app

Then open http://localhost:8089 to configure users and start the test, or run
headless with -u / -r / -t.

Each simulated user funds its own customer account in the ledger, then drives
payments through the orchestrator. Payments are small (10.00) so they clear risk
and settle rather than being held for review. Set CA_BUNDLE to a certificate
bundle if a local TLS-inspecting proxy breaks certificate verification.
"""

import os
import uuid

import requests
from locust import HttpUser, between, task

LEDGER_BASE_URL = os.getenv(
    "LEDGER_BASE_URL", "https://ledger-api-465847189589.europe-west2.run.app"
)
LEDGER_USERNAME = os.getenv("LEDGER_USERNAME", "admin")
LEDGER_PASSWORD = os.getenv("LEDGER_PASSWORD", "admin123")
CA_BUNDLE = os.getenv("CA_BUNDLE")  # optional path to a CA bundle


def _fund_account() -> str:
    """Create and fund a customer account in the ledger, returning its id."""
    session = requests.Session()
    if CA_BUNDLE:
        session.verify = CA_BUNDLE
    token = session.post(
        f"{LEDGER_BASE_URL}/auth/token",
        data={"username": LEDGER_USERNAME, "password": LEDGER_PASSWORD},
        timeout=30,
    ).json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    account = session.post(
        f"{LEDGER_BASE_URL}/accounts",
        json={"name": f"load-{uuid.uuid4().hex[:8]}"},
        headers=headers,
        timeout=30,
    ).json()
    session.post(
        f"{LEDGER_BASE_URL}/transactions",
        json={
            "idempotency_key": f"seed-{uuid.uuid4().hex}",
            "type": "deposit",
            "amount": "10000.00",
            "account_id": account["id"],
        },
        headers=headers,
        timeout=30,
    )
    return account["id"]


class OrchestratorUser(HttpUser):
    wait_time = between(0.1, 0.5)
    account_id = None
    last_payment_id = None

    def on_start(self):
        if CA_BUNDLE:
            self.client.verify = CA_BUNDLE
        self.account_id = _fund_account()

    @task(4)
    def create_payment(self):
        resp = self.client.post(
            "/payments",
            json={
                "account_id": self.account_id,
                "amount": "10.00",
                "destination": "acme",
            },
        )
        if resp.status_code == 201:
            self.last_payment_id = resp.json()["id"]

    @task(2)
    def get_payment(self):
        if self.last_payment_id:
            self.client.get(
                f"/payments/{self.last_payment_id}", name="/payments/[id]"
            )

    @task(1)
    def get_events(self):
        if self.last_payment_id:
            self.client.get(
                f"/payments/{self.last_payment_id}/events",
                name="/payments/[id]/events",
            )

    @task(1)
    def outbox_pending(self):
        self.client.get("/outbox/pending")

    @task(1)
    def health(self):
        self.client.get("/health")
