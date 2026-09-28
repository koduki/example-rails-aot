#!/usr/bin/env bash
set -euo pipefail
: "${BENCH_GCE_PROJECT:?set project}"
: "${BENCH_GCE_ZONE:?set zone}"
: "${BENCH_NETWORK:?set existing VPC}"
: "${BENCH_SUBNETWORK:?set existing subnet}"
region="${BENCH_GCE_ZONE%-*}"
gcloud compute networks describe "$BENCH_NETWORK" --project "$BENCH_GCE_PROJECT" --format='value(selfLink)'
gcloud compute networks subnets describe "$BENCH_SUBNETWORK" --region "$region" --project "$BENCH_GCE_PROJECT" --format='value(network)'
found=0
while IFS= read -r router; do
  [ -n "$router" ] || continue
  gcloud compute routers nats list --router "$router" --region "$region" --project "$BENCH_GCE_PROJECT" --format='table(name,sourceSubnetworkIpRangesToNat)'
  found=1
done < <(gcloud compute routers list --filter="region:$region" --project "$BENCH_GCE_PROJECT" --format='value(name)')
[ "$found" -eq 1 ] || { echo 'No router found in region; verify existing Cloud NAT.' >&2; exit 1; }
# Inspect NAT subnet coverage before applying Terraform.
