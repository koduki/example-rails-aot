#!/usr/bin/env bash
set -euo pipefail
exec > >(tee -a /var/log/startup-script.log) 2>&1
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y --no-install-recommends ca-certificates curl git build-essential python3 python3-pip python3-venv numactl gnupg iputils-ping
install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
chmod a+r /etc/apt/keyrings/docker.asc
. /etc/os-release
printf 'deb [arch=%s signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu %s stable\n' "$(dpkg --print-architecture)" "$VERSION_CODENAME" > /etc/apt/sources.list.d/docker.list
apt-get update -qq
apt-get install -y --no-install-recommends docker-ce docker-ce-cli containerd.io docker-buildx-plugin
systemctl enable --now docker
curl -fsSL https://packages.cloud.google.com/apt/doc/apt-key.gpg | gpg --dearmor -o /etc/apt/keyrings/google-cloud.gpg
printf 'deb [signed-by=/etc/apt/keyrings/google-cloud.gpg] https://packages.cloud.google.com/apt cloud-sdk main\n' > /etc/apt/sources.list.d/google-cloud-sdk.list
apt-get update -qq
apt-get install -y --no-install-recommends google-cloud-cli
test -f /sys/fs/cgroup/cgroup.controllers
lscpu
for c in /sys/devices/system/cpu/cpu[0-9]*; do cat "$c/topology/thread_siblings_list"; done
touch /var/run/startup-completed
