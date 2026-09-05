from datetime import datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.states import PaymentState


class PaymentCreate(BaseModel):
    account_id: UUID
    amount: Decimal = Field(gt=0, max_digits=12, decimal_places=2)
    destination: str = Field(min_length=1, max_length=200)


class PaymentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    account_id: UUID
    amount: Decimal
    destination: str
    state: PaymentState
    provider: str | None
    reserve_tx_id: UUID | None
    capture_tx_id: UUID | None
    release_tx_id: UUID | None
    correlation_id: UUID
    created_at: datetime
