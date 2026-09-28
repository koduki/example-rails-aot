output "app_instance_name" {
  value = google_compute_instance.bench["app"].name
}
output "app_internal_ip" {
  value = google_compute_instance.bench["app"].network_interface[0].network_ip
}
output "loadgen_instance_name" {
  value = google_compute_instance.bench["loadgen"].name
}
output "loadgen_internal_ip" {
  value = google_compute_instance.bench["loadgen"].network_interface[0].network_ip
}
output "zone" {
  value = var.zone
}
output "app_ssh_command" {
  value = "gcloud compute ssh ${google_compute_instance.bench["app"].name} --zone=${var.zone} --tunnel-through-iap --project=${var.project_id}"
}
output "loadgen_ssh_command" {
  value = "gcloud compute ssh ${google_compute_instance.bench["loadgen"].name} --zone=${var.zone} --tunnel-through-iap --project=${var.project_id}"
}
output "actions_service_account_email" {
  value = google_service_account.bench_actions.email
}
output "runner_service_account_email" {
  value = google_service_account.bench_runner.email
}
