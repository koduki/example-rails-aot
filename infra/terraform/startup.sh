#!/usr/bin/env bash
set -euo pipefail

LOG_FILE="/var/log/startup-script.log"
exec > >(tee -a "${LOG_FILE}") 2>&1

echo "[$(date -u --rfc-3339=seconds)] Starting benchmark runner initialization..."

export DEBIAN_FRONTEND=noninteractive

# 1. System packages update
apt-get update -qq
apt-get install -y --no-install-recommends \
  ca-certificates \
  curl \
  gnupg \
  lsb-release \
  git \
  jq \
  build-essential \
  python3 \
  python3-pip \
  python3-venv \
  numactl

# 2. Install Docker Engine (official repository)
install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
chmod a+r /etc/apt/keyrings/docker.asc

echo \
  "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu \
  $(. /etc/os-release && echo "${UBUNTU_CODENAME:-$VERSION_CODENAME}") stable" | \
  tee /etc/apt/sources.list.d/docker.list > /dev/null

apt-get update -qq
apt-get install -y --no-install-recommends \
  docker-ce \
  docker-ce-cli \
  containerd.io \
  docker-buildx-plugin \
  docker-compose-plugin

systemctl enable --now docker

# 3. Install Grafana k6 (official repository)
curl -fsSL https://dl.k6.io/key.gpg | gpg --dearmor -o /etc/apt/keyrings/k6.gpg
chmod a+r /etc/apt/keyrings/k6.gpg

echo \
  "deb [signed-by=/etc/apt/keyrings/k6.gpg] https://dl.k6.io/deb stable main" | \
  tee /etc/apt/sources.list.d/k6.list > /dev/null

apt-get update -qq
apt-get install -y --no-install-recommends k6

# 4. Verify cgroups v2 mount
if [ -f /sys/fs/cgroup/cgroup.controllers ]; then
  echo "[$(date -u --rfc-3339=seconds)] Verified: cgroups v2 unified hierarchy is active."
else
  echo "[$(date -u --rfc-3339=seconds)] WARNING: cgroups v2 unified hierarchy not detected."
fi

# 5. Log CPU topology and SMT sibling information
echo "--- CPU Architecture ---"
lscpu || true
echo "--- Thread Siblings List ---"
for c in /sys/devices/system/cpu/cpu[0-9]*; do
  if [ -f "${c}/topology/thread_siblings_list" ]; then
    echo "$(basename "${c}"): $(cat "${c}/topology/thread_siblings_list")"
  fi
done

# Mark initialization as complete
touch /var/run/startup-completed
echo "[$(date -u --rfc-3339=seconds)] Benchmark runner initialization completed successfully."
