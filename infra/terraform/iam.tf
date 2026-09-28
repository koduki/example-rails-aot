# -----------------------------------------------------------------------------
# 1. GitHub Actions Service Account (Least Privilege for IAP SSH orchestration)
# -----------------------------------------------------------------------------
resource "google_service_account" "bench_actions" {
  account_id   = "sa-bench-actions"
  display_name = "Service Account for Benchmark GitHub Actions"
  description  = "Used by GitHub Actions to connect to bench-runner-c3 via IAP SSH and retrieve benchmark artifacts."
}

resource "google_project_iam_member" "actions_roles" {
  for_each = toset([
    "roles/iap.tunnelResourceAccessor",
    "roles/compute.osAdminLogin",
    "roles/compute.viewer",
  ])
  project = var.project_id
  role    = each.key
  member  = "serviceAccount:${google_service_account.bench_actions.email}"
}

# Allow Actions SA to act as the runner SA when connecting via OS Login
resource "google_service_account_iam_member" "actions_actas_runner" {
  service_account_id = google_service_account.bench_runner.name
  role               = "roles/iam.serviceAccountUser"
  member             = "serviceAccount:${google_service_account.bench_actions.email}"
}

# -----------------------------------------------------------------------------
# 2. VM Attached Service Account (Least Privilege for logging and metrics)
# -----------------------------------------------------------------------------
resource "google_service_account" "bench_runner" {
  account_id   = "sa-bench-runner"
  display_name = "Service Account for Benchmark Runner VM"
  description  = "Attached to the bench-runner-c3 instance. Restricted to logging and monitoring only."
}

resource "google_project_iam_member" "runner_roles" {
  for_each = toset([
    "roles/logging.logWriter",
    "roles/monitoring.metricWriter",
  ])
  project = var.project_id
  role    = each.key
  member  = "serviceAccount:${google_service_account.bench_runner.email}"
}

# The app VM orchestrates the tester over private SSH using its attached identity.
resource "google_project_iam_member" "app_to_loadgen" {
  for_each = toset(["roles/compute.osLogin", "roles/compute.viewer"])
  project  = var.project_id
  role     = each.key
  member   = "serviceAccount:${google_service_account.bench_runner.email}"
}
resource "google_service_account_iam_member" "runner_actas_self" {
  service_account_id = google_service_account.bench_runner.name
  role               = "roles/iam.serviceAccountUser"
  member             = "serviceAccount:${google_service_account.bench_runner.email}"
}
