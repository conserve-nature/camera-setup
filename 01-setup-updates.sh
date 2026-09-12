#!/bin/bash
# Install recurring, unattended host package and major Debian release upgrades.
set -euo pipefail
if [[ ${EUID} -ne 0 ]]; then
    echo "Run with sudo: sudo $0" >&2
    exit 1
fi
script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
. /etc/os-release
if [[ ${ID} != debian ]] || [[ $(dpkg --print-architecture) != arm64 && $(dpkg --print-architecture) != amd64 ]]; then
    echo 'Supported hosts: Debian / 64-bit Raspberry Pi OS (arm64 or amd64).' >&2
    exit 1
fi
# Share the updater lock, including manual runs and systemd's "activating" state.
install -d -m 0700 /var/lib/trail-camera-updates
exec 9>/var/lib/trail-camera-updates/lock
if ! flock -n 9; then
    echo 'The updater is running; retry installation after it finishes.' >&2
    exit 1
fi
for dependency in python3 gpgv; do
    if ! command -v "$dependency" >/dev/null; then
        echo "Missing $dependency; install python3 gpgv debian-archive-keyring first." >&2
        exit 1
    fi
done
install -d -m 0755 /usr/local/lib/trail-camera
install -d -m 0700 /var/lib/trail-camera-updates
install -m 0755 "$script_dir/update-os.py" /usr/local/lib/trail-camera/update-os.py
if [[ -r /proc/device-tree/model ]] && [[ $(tr -d '\0' < /proc/device-tree/model) == Raspberry\ Pi* ]]; then
    # This appliance has a fixed IMX500. Explicit selection survives firmware
    # auto-detection regressions; the helper records any required reboot.
    python3 "$script_dir/configure-imx500.py"
fi
if [[ ! -e /etc/trail-camera-updates.json ]]; then
    cat > /etc/trail-camera-updates.json <<'CONFIG'
{
  "automatic_major": true,
  "automatic_reboot": true
}
CONFIG
    chmod 0644 /etc/trail-camera-updates.json
fi
install -d -m 0755 /etc/systemd/journald.conf.d
cat > /etc/systemd/journald.conf.d/90-trail-camera-updates.conf <<'JOURNAL'
[Journal]
Storage=persistent
SystemMaxUse=64M
MaxRetentionSec=14day
JOURNAL
systemd-tmpfiles --create --prefix /var/log/journal
systemctl restart systemd-journald
journalctl --flush
cat > /etc/systemd/system/trail-camera-update.service <<'SERVICE'
[Unit]
Description=Trail camera unattended package and major OS upgrades
Wants=network-online.target
After=network-online.target apt-daily.service apt-daily-upgrade.service
ConditionPathExists=/etc/debian_version

[Service]
Type=oneshot
ExecStart=/usr/local/lib/trail-camera/update-os.py
TimeoutStartSec=infinity
# Let an in-progress package transaction finish during ordinary shutdown.
TimeoutStopSec=infinity
KillSignal=SIGCONT
KillMode=control-group
Nice=10
IOSchedulingClass=best-effort
IOSchedulingPriority=7
StandardOutput=journal
StandardError=journal
SERVICE
cat > /etc/systemd/system/trail-camera-update.timer <<'TIMER'
[Unit]
Description=Daily trail camera host maintenance

[Timer]
OnBootSec=10m
OnCalendar=*-*-* 03:00:00
RandomizedDelaySec=30m
Persistent=true

[Install]
WantedBy=timers.target
TIMER
# This job owns scheduling. Do not stop an already-running APT service.
systemctl disable --now apt-daily.timer apt-daily-upgrade.timer
systemctl daemon-reload
exec 9>&-
systemctl enable --now trail-camera-update.timer
printf '%s\n' 'Installed. Updates run after boot and daily at 03:00–03:30 device-local time.'
printf '%s\n' 'Preview: sudo /usr/local/lib/trail-camera/update-os.py --check'
printf '%s\n' 'Run now: sudo systemctl start --no-block trail-camera-update.service'
