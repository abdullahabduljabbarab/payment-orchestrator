terraform {
  required_version = ">= 1.5"
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 5.0"
    }
  }
}

provider "google" {
  project = var.project_id
  region  = var.region
}

# The orchestrator layers onto the ledger's project. The Cloud SQL instance is
# owned and provisioned by the ledger; the orchestrator takes its own database
# and user on that instance rather than standing up a second server. This data
# source ties the two together: the ledger must be applied first.
data "google_sql_database_instance" "ledger_db" {
  name = var.sql_instance_name
}

resource "google_sql_database" "payments" {
  name     = "payments"
  instance = data.google_sql_database_instance.ledger_db.name
}

resource "google_sql_user" "orchestrator" {
  name     = "orchestrator"
  instance = data.google_sql_database_instance.ledger_db.name
  password = var.orchestrator_db_password
}

resource "google_artifact_registry_repository" "payment_orchestrator" {
  location      = var.region
  repository_id = "payment-orchestrator"
  format        = "DOCKER"
}

# The full connection string is held as one secret rather than just the
# password, so Cloud Run never sees a plaintext DATABASE_URL. The socket path
# points at the shared Cloud SQL instance the ledger owns.
resource "google_secret_manager_secret" "orchestrator_database_url" {
  secret_id = "orchestrator-database-url"

  replication {
    auto {}
  }
}

resource "google_secret_manager_secret_version" "orchestrator_database_url" {
  secret      = google_secret_manager_secret.orchestrator_database_url.id
  secret_data = "postgresql://${google_sql_user.orchestrator.name}:${var.orchestrator_db_password}@/${google_sql_database.payments.name}?host=/cloudsql/${data.google_sql_database_instance.ledger_db.connection_name}"
}

# The credential the orchestrator uses to authenticate to the ledger API as it
# posts reserve, capture and release transactions. Kept in Secret Manager and
# injected at runtime, never set as a plaintext env var.
resource "google_secret_manager_secret" "ledger_admin_password" {
  secret_id = "ledger-admin-password"

  replication {
    auto {}
  }
}

resource "google_secret_manager_secret_version" "ledger_admin_password" {
  secret      = google_secret_manager_secret.ledger_admin_password.id
  secret_data = var.ledger_admin_password
}

resource "google_service_account" "cloud_run" {
  account_id   = "payment-orchestrator-runner"
  display_name = "Payment Orchestrator Cloud Run"
}

resource "google_secret_manager_secret_iam_member" "cloud_run_database_url" {
  secret_id = google_secret_manager_secret.orchestrator_database_url.id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.cloud_run.email}"
}

resource "google_secret_manager_secret_iam_member" "cloud_run_ledger_password" {
  secret_id = google_secret_manager_secret.ledger_admin_password.id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.cloud_run.email}"
}

resource "google_pubsub_topic" "payment_events" {
  name = "payment-events"
}

resource "google_pubsub_subscription" "payment_events_sub" {
  name  = "payment-events-sub"
  topic = google_pubsub_topic.payment_events.id

  ack_deadline_seconds = 20

  retry_policy {
    minimum_backoff = "10s"
    maximum_backoff = "600s"
  }
}

resource "google_pubsub_topic_iam_member" "cloud_run_publish" {
  topic  = google_pubsub_topic.payment_events.id
  role   = "roles/pubsub.publisher"
  member = "serviceAccount:${google_service_account.cloud_run.email}"
}

resource "google_cloud_run_v2_service" "payment_orchestrator" {
  name     = "payment-orchestrator"
  location = var.region

  template {
    service_account = google_service_account.cloud_run.email

    containers {
      image = "${var.region}-docker.pkg.dev/${var.project_id}/payment-orchestrator/payment-orchestrator:latest"

      ports {
        container_port = 8080
      }

      env {
        name = "DATABASE_URL"
        value_source {
          secret_key_ref {
            secret  = google_secret_manager_secret.orchestrator_database_url.secret_id
            version = "latest"
          }
        }
      }

      env {
        name = "LEDGER_PASSWORD"
        value_source {
          secret_key_ref {
            secret  = google_secret_manager_secret.ledger_admin_password.secret_id
            version = "latest"
          }
        }
      }

      env {
        name  = "ENVIRONMENT"
        value = "production"
      }

      env {
        name  = "LEDGER_BASE_URL"
        value = var.ledger_base_url
      }

      env {
        name  = "RISK_BASE_URL"
        value = var.risk_base_url
      }

      env {
        name  = "LEDGER_USERNAME"
        value = var.ledger_username
      }

      env {
        name  = "SUSPENSE_ACCOUNT_ID"
        value = var.suspense_account_id
      }

      env {
        name  = "SETTLEMENT_ACCOUNT_ID"
        value = var.settlement_account_id
      }

      env {
        name  = "PUBSUB_TOPIC"
        value = google_pubsub_topic.payment_events.id
      }

      resources {
        limits = {
          cpu    = "1"
          memory = "512Mi"
        }
      }
    }

    scaling {
      min_instance_count = 0
      max_instance_count = 3
    }

    volumes {
      name = "cloudsql"
      cloud_sql_instance {
        instances = [data.google_sql_database_instance.ledger_db.connection_name]
      }
    }
  }
}

resource "google_cloud_run_v2_service_iam_member" "public" {
  name     = google_cloud_run_v2_service.payment_orchestrator.name
  location = var.region
  role     = "roles/run.invoker"
  member   = "allUsers"
}
