#!/usr/bin/env bash
set -euo pipefail
exec > >(tee -a /var/log/startup-script.log) 2>&1
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y --no-install-recommends ca-certificates curl gnupg git python3 jq sysstat
install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://dl.k6.io/key.gpg | gpg --dearmor -o /etc/apt/keyrings/k6.gpg
chmod a+r /etc/apt/keyrings/k6.gpg
printf 'deb [signed-by=/etc/apt/keyrings/k6.gpg] https://dl.k6.io/deb stable main\n' > /etc/apt/sources.list.d/k6.list
apt-get update -qq
apt-get install -y --no-install-recommends k6
lscpu
touch /var/run/startup-completed
