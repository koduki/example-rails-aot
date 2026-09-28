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

data "google_compute_network" "existing" {
  name    = var.network_name
  project = var.project_id
}

data "google_compute_subnetwork" "existing" {
  name    = var.subnetwork_name
  region  = var.region
  project = var.project_id
}

locals {
  roles = {
    app     = { name = "bench-app-c3", tag = "bench-app", startup = "startup-app.sh" }
    loadgen = { name = "bench-loadgen-c3", tag = "bench-loadgen", startup = "startup-loadgen.sh" }
  }
}

resource "google_compute_instance" "bench" {
  for_each     = local.roles
  name         = each.value.name
  machine_type = "c3-standard-4"
  zone         = var.zone

  boot_disk {
    initialize_params {
      image = "ubuntu-os-cloud/ubuntu-2404-lts-amd64"
      size  = var.disk_size_gb
      type  = "pd-ssd"
    }
  }

  network_interface {
    network    = data.google_compute_network.existing.self_link
    subnetwork = data.google_compute_subnetwork.existing.self_link
    # No access_config: outbound uses the existing Cloud NAT.
  }

  metadata                = { enable-oslogin = "TRUE" }
  metadata_startup_script = file("${path.module}/${each.value.startup}")
  service_account {
    email  = google_service_account.bench_runner.email
    scopes = ["cloud-platform"]
  }
  tags = [each.value.tag]
}
