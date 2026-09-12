# ipaw.local application record

## Baseline — 2026-09-13, Australia/Sydney

- Hardware: Raspberry Pi Zero 2 W Rev 1.0; arm64.
- OS: Debian GNU/Linux 13 (Trixie), DEBIAN_VERSION_FULL=13.5.
- Kernel: 6.18.34+rpt-rpi-v8.
- Boot ID: 7d0e9d00-6f5a-4c4e-95a1-dd6fdcf40fe3.
- APT sources: debian.sources (trixie, trixie-updates, trixie-security),
  raspi.sources (trixie); no custom pinning.
- Root: 26 GiB available; /boot/firmware: 430 MiB available.
- dpkg audit: clean; failed systemd units: none.
- Camera: imx500 detected, 2028x1520 and 4056x3040 modes.
- Docker is not installed yet.
- Both standard APT timers enabled. Journal directory existed, but the Raspberry Pi
  vendor configuration actually selected volatile storage (discovered after reboot).

## Applied

Installed the updater and enabled its boot/daily timer; disabled apt-daily.timer and
apt-daily-upgrade.timer. The real unattended update ran 09:10–09:18 AEST and installed
132 upgrades and 10 new packages, with zero removals or held-back packages. It
automatically rebooted into Debian 13.7 / kernel 6.18.39+rpt-rpi-v8; SSH, dpkg audit,
and systemd health passed. Boot ID changed to 42a3f4c4-cca3-414b-9366-14e75958f3f5.
Signed Debian stable metadata identified Trixie, so no real major jump was available.

That update exposed an upstream IMX500 autodetection defect: firmware hash
465206a286cc1a7b13c2e86bc199f74dde7a8309 exactly matches the affected September 7
firmware. The hardware smoke test failed because the IMX500 device-tree node was
absent. Raspberry Pi fixed this upstream on September 11:
https://github.com/raspberrypi/firmware/commit/ae2a7dc5330b7ea2c7107e5c4cb6b2691355bb9c

The installer now explicitly configures this fixed IMX500 hardware in a managed
config.txt block, with a one-time original backup at
/boot/firmware/config.txt.trail-camera-imx500.bak. It also enables persistent journals
capped at 64 MiB and 14 days. Both changes applied. The second automatic reboot
completed at 09:25 AEST, boot ID 0d47b26c-5744-4778-99f2-70d0da9ff423.
The same hardware smoke test then passed: IMX500 enumerated and a 4056x3040 still
was received successfully (image data discarded to /dev/null). dpkg audit was clean,
no systemd units failed, and the previous boot's updater logs were readable.
All 48 unit tests pass on both the development host and Pi.

Final reinstall preserved SHA-256 hashes of the updater, user settings, systemd
units, journald settings, and camera boot config. An attempted reinstall during the
first update correctly refused the held lock. Final no-change maintenance completed
at 09:28:24 AEST with Result=success / ExecMainStatus=0, packages_changed=false,
the same boot ID, and no pending reboot marker. The boot/daily timer remains enabled.

## Verification limits

Real major-version migration cannot be tested against a newer stable release today.
Automated tests exercise migration parsing, adjacent release checks, source replay,
interrupted dpkg dependency repair, and final-reboot recovery using isolated state
and mocked package commands. They do not prove future kernel/camera compatibility
or automatic recovery from an unbootable OS. No destructive power-loss test performed.

## Offline defaults and manual maintenance — 2026-09-13

User clarified that cameras normally operate offline and expressly prohibited
network disconnection testing on this SSH device. No interfaces were disabled,
NetworkManager was not reloaded/restarted, and the device was not rebooted.

- Applied revised 01-setup-updates.sh: trail-camera-update.timer, apt-daily.timer,
  and apt-daily-upgrade.timer are disabled. Update service remained inactive.
- Applied setup.sh and 02-setup-network.sh: early trail-camera-loopback.service is
  enabled for next boot but not started; IPv6 lo/default settings and NetworkManager
  loopback ownership changes are configuration only until the next normal boot.
- Preserved existing files-first NSS configuration. Updated local hosts mappings,
  cloud-init policy and Debian hosts template; one-time .trail-camera-network.bak
  backups retain replaced files.
- Connected TCP round trips over 127.0.0.1 and ::1 passed before and after setup;
  localhost and local hostname resolve to loopback. No offline failure was reproduced.
- systemd-analyze verify passed for the loopback service and updater units.
  configure-network.py --check reported no remaining changes after application.
- NetworkManager stayed PID 603, active since 09:25:57 AEST; boot ID remained
  0d47b26c-5744-4778-99f2-70d0da9ff423. Wi-Fi remained connected with its addresses intact.
- 78 controlled tests pass, including bootstrap/sudo/clone failures, safe reruns,
  local hosts/cloud-init transforms, and network configuration idempotency.

The exact published curl bootstrap and its clean-checkout rerun will be verified
after publication. Boot-time and disconnected behavior are left for the user's
other device; tests/loopback-smoke.py changes no network configuration.
