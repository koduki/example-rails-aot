# -----------------------------------------------------------------------------
# Firewall rule allowing SSH (tcp:22) ONLY from Google Cloud IAP IP range
# -----------------------------------------------------------------------------
resource "google_compute_firewall" "allow_iap_ssh" {
  name        = "allow-iap-ssh-bench"
  network     = "default"
  description = "Allows SSH ingress exclusively from Identity-Aware Proxy (IAP) range for secure access without public IP."

  allow {
    protocol = "tcp"
    ports    = ["22"]
  }

  # Official Google Cloud IAP CIDR range
  source_ranges = ["35.235.240.0/20"]
  target_tags   = ["bench-node"]
}
