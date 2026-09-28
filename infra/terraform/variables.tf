variable "project_id" {
  description = "The GCP project ID to deploy resources into."
  type        = string
  default     = "sandbox-svc-dev-8rra"
}

variable "region" {
  description = "The GCP region for deployment."
  type        = string
  default     = "asia-northeast1"
}

variable "zone" {
  description = "The GCP zone for the benchmark instance."
  type        = string
  default     = "asia-northeast1-b"
}

variable "machine_type" {
  description = "Compute Engine machine type. c3-standard-4 (Intel Xeon Sapphire Rapids) is recommended for SMT isolation."
  type        = string
  default     = "c3-standard-4"
}

variable "disk_size_gb" {
  description = "Boot disk size in GB. Sufficient for multiple Docker image builds."
  type        = number
  default     = 60
}

variable "instance_name" {
  description = "Name of the benchmark runner GCE instance."
  type        = string
  default     = "bench-runner-c3"
}
