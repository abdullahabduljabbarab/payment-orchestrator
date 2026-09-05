output "cloud_run_url" {
  description = "Live orchestrator URL"
  value       = google_cloud_run_v2_service.payment_orchestrator.uri
}

output "artifact_registry" {
  description = "Docker image registry path"
  value       = "${var.region}-docker.pkg.dev/${var.project_id}/payment-orchestrator"
}

output "pubsub_topic" {
  description = "Topic the orchestrator publishes payment events to"
  value       = google_pubsub_topic.payment_events.id
}

output "pubsub_subscription" {
  description = "Subscription consumers pull payment events from"
  value       = google_pubsub_subscription.payment_events_sub.name
}

output "payments_database" {
  description = "Orchestrator database on the shared Cloud SQL instance"
  value       = google_sql_database.payments.name
}
