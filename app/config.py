import os

# Database for the orchestrator's own state.
DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://payments:payments@localhost:5433/payments",
)

# The ledger this orchestrator settles against.
LEDGER_BASE_URL = os.getenv("LEDGER_BASE_URL", "http://localhost:8000")

# The risk engine this orchestrator asks to decide a payment before reserving.
# If it is unreachable the payment is held for review, never allowed.
RISK_BASE_URL = os.getenv("RISK_BASE_URL", "http://localhost:8080")
LEDGER_USERNAME = os.getenv("LEDGER_USERNAME", "admin")
LEDGER_PASSWORD = os.getenv("LEDGER_PASSWORD", "admin123")

# System accounts in the ledger that funds move through.
# Reserve moves customer -> suspense; capture moves suspense -> settlement;
# release moves suspense -> customer. These accounts are provisioned and owned
# by the ledger (ledger migration 005) with fixed IDs; the orchestrator only
# holds their identifiers and settles through the ledger API.
SUSPENSE_ACCOUNT_ID = os.getenv(
    "SUSPENSE_ACCOUNT_ID", "a0000000-0000-4000-8000-000000000001"
)
SETTLEMENT_ACCOUNT_ID = os.getenv(
    "SETTLEMENT_ACCOUNT_ID", "a0000000-0000-4000-8000-000000000002"
)

# Event publishing. Unset means the log transport is used (see publisher).
PUBSUB_TOPIC = os.getenv("PUBSUB_TOPIC", "")

ENVIRONMENT = os.getenv("ENVIRONMENT", "local")
