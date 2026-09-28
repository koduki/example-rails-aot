output "instance_name" {
  description = "Name of the created benchmark runner VM."
  value       = google_compute_instance.bench_runner.name
}

output "instance_id" {
  description = "GCE instance ID."
  value       = google_compute_instance.bench_runner.instance_id
}

output "internal_ip" {
  description = "Internal private IP address of the benchmark runner VM."
  value       = google_compute_instance.bench_runner.network_interface[0].network_ip
}

output "actions_service_account_email" {
  description = "Email of the service account used by GitHub Actions."
  value       = google_service_account.bench_actions.email
}

output "runner_service_account_email" {
  description = "Email of the service account attached to the benchmark runner VM."
  value       = google_service_account.bench_runner.email
}

output "iap_ssh_command" {
  description = "Command to connect to the runner VM via IAP SSH."
  value       = "gcloud compute ssh ${google_compute_instance.bench_runner.name} --zone=${var.zone} --tunnel-through-iap --project=${var.project_id}"
}
