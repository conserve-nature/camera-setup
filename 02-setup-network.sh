#!/bin/bash
# Persist offline networking without changing active interfaces or restarting services.
set -euo pipefail
if [[ $EUID -ne 0 ]]; then
    echo 'Run as root (sudo).' >&2
    exit 1
fi
script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
for command in python3 ip sysctl systemctl; do
    command -v "$command" >/dev/null || { echo "Missing prerequisite: $command" >&2; exit 1; }
done
python3 "$script_dir/configure-network.py"
systemctl daemon-reload
systemctl enable trail-camera-loopback.service
echo 'Offline loopback setup saved for the next boot. Active networking is unchanged.'
