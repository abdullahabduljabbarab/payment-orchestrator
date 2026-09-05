variable "project_id" {
  description = "GCP project ID. The orchestrator shares the ledger's project."
  type        = string
  default     = "ledger-api-507618"
}

variable "region" {
  description = "GCP region for all resources"
  type        = string
  default     = "europe-west2"
}

variable "sql_instance_name" {
  description = "Name of the ledger's Cloud SQL instance the orchestrator takes its database on"
  type        = string
  default     = "ledger-db"
}

variable "orchestrator_db_password" {
  description = "Password for the orchestrator's database user. Stored in Secret Manager as part of the connection string and injected into Cloud Run, never set as a plaintext env var."
  type        = string
  sensitive   = true
}

variable "ledger_admin_password" {
  description = "Password the orchestrator uses to authenticate to the ledger API. Stored in Secret Manager and injected into Cloud Run."
  type        = string
  sensitive   = true
}

variable "ledger_base_url" {
  description = "Base URL of the ledger the orchestrator settles against"
  type        = string
  default     = "https://ledger-api-465847189589.europe-west2.run.app"
}

variable "risk_base_url" {
  description = "Base URL of the risk engine the orchestrator asks to decide a payment"
  type        = string
  default     = "https://risk-engine-eppidgbmxa-nw.a.run.app"
}

variable "ledger_username" {
  description = "Username the orchestrator authenticates to the ledger with"
  type        = string
  default     = "admin"
}

# The suspense and settlement accounts are provisioned and owned by the ledger
# (ledger migration 005) with fixed IDs. The orchestrator only holds their
# identifiers and moves funds through them via the ledger API.
variable "suspense_account_id" {
  description = "Ledger Payment Suspense account ID"
  type        = string
  default     = "a0000000-0000-4000-8000-000000000001"
}

variable "settlement_account_id" {
  description = "Ledger Settlement Clearing account ID"
  type        = string
  default     = "a0000000-0000-4000-8000-000000000002"
}
