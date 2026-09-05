import os

# Database for the orchestrator's own state.
DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://payments:payments@localhost:5433/payments",
)

# The ledger this orchestrator settles against.
LEDGER_BASE_URL = os.getenv("LEDGER_BASE_URL", "http://localhost:8000")
LEDGER_USERNAME = os.getenv("LEDGER_USERNAME", "admin")
LEDGER_PASSWORD = os.getenv("LEDGER_PASSWORD", "admin123")

# System accounts in the ledger that funds move through.
# Reserve moves customer -> suspense; capture moves suspense -> settlement;
# release moves suspense -> customer. These accounts must exist in the ledger.
SUSPENSE_ACCOUNT_ID = os.getenv("SUSPENSE_ACCOUNT_ID", "")
SETTLEMENT_ACCOUNT_ID = os.getenv("SETTLEMENT_ACCOUNT_ID", "")

# Event publishing. Unset means the log transport is used (see publisher).
PUBSUB_TOPIC = os.getenv("PUBSUB_TOPIC", "")

ENVIRONMENT = os.getenv("ENVIRONMENT", "local")
