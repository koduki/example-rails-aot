terraform {
  required_version = ">= 1.5.0"
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 6.0"
    }
  }
}

provider "google" {
  project = var.project_id
  region  = var.region
  zone    = var.zone
}

# Dedicated GCE instance for benchmark execution
resource "google_compute_instance" "bench_runner" {
  name         = var.instance_name
  machine_type = var.machine_type
  zone         = var.zone

  boot_disk {
    initialize_params {
      image = "ubuntu-os-cloud/ubuntu-2404-lts-amd64"
      size  = var.disk_size_gb
      type  = "pd-ssd"
    }
  }

  # Completely private interface (no external IP / access_config block omitted)
  # Uses existing default VPC and Cloud NAT for outbound traffic
  network_interface {
    network    = "default"
    subnetwork = "default"
  }

  # OS Login enabled for centralized IAM-based SSH management
  metadata = {
    enable-oslogin = "TRUE"
  }

  metadata_startup_script = file("${path.module}/startup.sh")

  service_account {
    email  = google_service_account.bench_runner.email
    scopes = ["cloud-platform"]
  }

  tags = ["bench-node"]

  # Protect against accidental deletion during active measurements
  lifecycle {
    create_before_destroy = false
  }
}
