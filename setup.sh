#!/bin/bash
# Offline configuration from a complete local checkout. No downloads or upgrades.
set -euo pipefail
umask 022
if [[ $(id -u) -ne 0 ]]; then
    echo "Run with sudo: sudo /bin/bash $0" >&2
    exit 1
fi
script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
# Validate the entire installation bundle before the first script changes the host.
for payload in 01-setup-updates.sh update-os.py configure-imx500.py \
               02-setup-network.sh configure-network.py; do
    if [[ ! -f $script_dir/$payload || ! -r $script_dir/$payload ]]; then
        echo "Missing setup payload: $script_dir/$payload" >&2
        exit 1
    fi
done
for step in 01-setup-updates.sh 02-setup-network.sh; do
    printf 'Applying %s\n' "$step"
    /bin/bash "$script_dir/$step"
done
printf '%s\n' 'Configuration complete. OS updates and reboot are operator-controlled.'
