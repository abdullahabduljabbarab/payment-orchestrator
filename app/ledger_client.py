"""Client for the ledger's transaction API.

The orchestrator only ever moves money through three operations, each keyed
deterministically so the ledger's own idempotency makes a retry a no-op:

    reserve   customer  -> suspense       key payment:{id}:reserve
    capture   suspense  -> settlement     key payment:{id}:capture
    release   suspense  -> customer       key payment:{id}:release

A reserve is the only operation that can fail for lack of funds, because
capture and release move money that is already held in suspense.
"""

from decimal import Decimal
from typing import Protocol
from uuid import UUID

import httpx


class LedgerError(Exception):
    pass


class InsufficientFunds(LedgerError):
    pass


class LedgerUnavailable(LedgerError):
    pass


def reserve_key(payment_id: UUID) -> str:
    return f"payment:{payment_id}:reserve"


def capture_key(payment_id: UUID) -> str:
    return f"payment:{payment_id}:capture"


def release_key(payment_id: UUID) -> str:
    return f"payment:{payment_id}:release"


class LedgerClient(Protocol):
    def reserve(self, payment_id: UUID, account_id: UUID, amount: Decimal) -> UUID: ...

    def capture(self, payment_id: UUID, amount: Decimal) -> UUID: ...

    def release(self, payment_id: UUID, account_id: UUID, amount: Decimal) -> UUID: ...


class HttpLedgerClient:
    def __init__(
        self,
        base_url: str,
        username: str,
        password: str,
        suspense_account_id: UUID,
        settlement_account_id: UUID,
        timeout: float = 30.0,
    ):
        self._base_url = base_url.rstrip("/")
        self._username = username
        self._password = password
        self._suspense = suspense_account_id
        self._settlement = settlement_account_id
        self._client = httpx.Client(timeout=timeout)
        self._token: str | None = None

    def _authenticate(self) -> str:
        try:
            resp = self._client.post(
                f"{self._base_url}/auth/token",
                data={"username": self._username, "password": self._password},
            )
        except httpx.HTTPError as e:
            raise LedgerUnavailable(str(e)) from e
        if resp.status_code != 200:
            raise LedgerError(f"auth failed: {resp.status_code}")
        return resp.json()["access_token"]

    def _headers(self) -> dict:
        if self._token is None:
            self._token = self._authenticate()
        return {"Authorization": f"Bearer {self._token}"}

    def _transfer(
        self,
        from_account_id: UUID,
        to_account_id: UUID,
        amount: Decimal,
        idempotency_key: str,
    ) -> UUID:
        body = {
            "idempotency_key": idempotency_key,
            "type": "transfer",
            "amount": str(amount),
            "from_account_id": str(from_account_id),
            "to_account_id": str(to_account_id),
        }
        try:
            resp = self._client.post(
                f"{self._base_url}/transactions", json=body, headers=self._headers()
            )
            if resp.status_code == 401:
                # Token expired: re-authenticate once and retry.
                self._token = None
                resp = self._client.post(
                    f"{self._base_url}/transactions",
                    json=body,
                    headers=self._headers(),
                )
        except httpx.HTTPError as e:
            raise LedgerUnavailable(str(e)) from e

        if resp.status_code == 201:
            return UUID(resp.json()["id"])
        if resp.status_code == 422:
            detail = str(resp.json().get("detail", "")).lower()
            if "insufficient" in detail:
                raise InsufficientFunds(detail)
            raise LedgerError(f"unprocessable: {detail}")
        if resp.status_code >= 500:
            raise LedgerUnavailable(f"ledger {resp.status_code}")
        raise LedgerError(f"unexpected {resp.status_code}: {resp.text}")

    def reserve(self, payment_id: UUID, account_id: UUID, amount: Decimal) -> UUID:
        return self._transfer(
            account_id, self._suspense, amount, reserve_key(payment_id)
        )

    def capture(self, payment_id: UUID, amount: Decimal) -> UUID:
        return self._transfer(
            self._suspense, self._settlement, amount, capture_key(payment_id)
        )

    def release(self, payment_id: UUID, account_id: UUID, amount: Decimal) -> UUID:
        return self._transfer(
            self._suspense, account_id, amount, release_key(payment_id)
        )
