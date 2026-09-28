resource "google_compute_firewall" "allow_iap_ssh" {
  name          = "allow-iap-ssh-bench"
  network       = data.google_compute_network.existing.self_link
  source_ranges = ["35.235.240.0/20"]
  target_tags   = ["bench-app", "bench-loadgen"]
  allow {
    protocol = "tcp"
    ports    = ["22"]
  }
}
resource "google_compute_firewall" "allow_benchmark" {
  name        = "allow-bench-loadgen-to-app"
  network     = data.google_compute_network.existing.self_link
  source_tags = ["bench-loadgen"]
  target_tags = ["bench-app"]
  allow {
    protocol = "tcp"
    ports    = ["3000"]
  }
}
