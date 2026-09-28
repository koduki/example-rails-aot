variable "project_id" {
  description = "GCP project ID. Supply via tfvars or -var."
  type        = string
}
variable "network_name" {
  description = "Existing VPC name. Cloud NAT is managed outside this module."
  type        = string
}
variable "subnetwork_name" {
  description = "Existing subnet in the selected region."
  type        = string
}
variable "region" {
  type    = string
  default = "asia-northeast1"
}
variable "zone" {
  type    = string
  default = "asia-northeast1-b"
}
variable "disk_size_gb" {
  type    = number
  default = 60
}
