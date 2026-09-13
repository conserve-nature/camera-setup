# Automated host updates

## Plan and specification

- [x] Inspect repository, device OS, architecture, repositories, disk space, and timers.
- [x] Review upstream Debian and Raspberry Pi upgrade guidance and check the plan against device configuration.
- [x] Build an idempotent installer and unattended updater with a daily systemd timer.
- [x] Verify source migration, release guards, recovery, and noninteractive commands with automated tests.
- [x] Install on ipaw.local, execute a real package update, verify reboot handling and repeat-install behavior.
- [x] Record applied changes and verification limits; commit and push the finished repository.

The device is Debian 13 (trixie), arm64, with Debian and Raspberry Pi APT archives.
The updater will first fully update the current release, then discover Debian stable from
cryptographically verified release metadata. It will automatically attempt an adjacent
major release when every enabled archive provides the target suite. It will keep
codename-pinned sources, reject unknown source layouts for major migration, preserve
local configuration, suppress interactive prompts, and reboot after actual package
changes (including kernel/firmware changes which do not always set reboot-required).
A persistent transaction records major upgrade progress and keeps source/config/package
metadata backups. Failed or interrupted upgrades retain the target sources for retry;
there is no claim of automatic rollback. Concurrent runs use a lock and APT lock waits.

Original defaults (superseded by the offline requirements below): run 10 minutes after boot and daily at 03:00 device-local time, each with up to 30 minutes jitter; automatic
major upgrades and reboot enabled. Manual check mode previews package changes and source
migration without installing packages or modifying live APT sources. Installer and updater
must be rerunnable. Unknown future release-specific migrations cannot be proven today;
Raspberry Pi does not officially support in-place major upgrades.

## Review

48 automated tests pass on the development host and the device. The device preview passed with signed metadata and target
architecture checks. Installer applied and systemd units validated; real package
update completed and automatically rebooted. The installer also correctly refused a
concurrent reinstall. An upstream firmware camera regression was diagnosed and fixed
with explicit IMX500 configuration; enumeration and full-resolution capture passed
after a second automatic reboot. Persistent logs were verified across that reboot.
Repeat-install file hashes were unchanged. The final no-change update exited zero,
cleared the prior reboot marker and preserved the boot ID. There are no failed
systemd units and dpkg audit is clean. Major upgrades are covered by controlled tests,
not a live transition: the device already runs the latest Debian stable major release.

## Re-plan after device verification regression

The package update completed and rebooted into Debian 13.7 / kernel 6.18.39.
SSH, dpkg and systemd health checks passed, but the IMX500 stopped enumerating.
The sensor node is absent from the boot device tree even though camera_auto_detect=1
and config.txt was not changed. Do not mark ready until camera functionality returns.

- [x] Reproduce the camera failure with a hardware smoke test.
- [x] Diagnose firmware autodetection / device-tree change and implement a durable fix.
- [x] Reboot and pass the same camera capture test on the new kernel.
- [x] Ensure updater logs survive reboot (the existing journal directory was insufficient).
- [x] Finish repeat-install and no-change update verification and document results.
- [x] Commit and push the verified scripts and application record.

Verified implementation published to origin/main as e67ded2.

## Offline operation, manual maintenance, and bootstrap installer

The camera normally runs offline. Keep the update service for explicit operator
invocation and optional automation; disable boot/daily scheduling on setup, including
migration of the already-enabled timer on ipaw.local. Setup must finish without an
OS upgrade or reboot. A curl entrypoint will install Git and prerequisites, clone
(or safely fast-forward) the official repository, then invoke an explicit setup
orchestrator. Rerunning local setup should not require Internet access.

Ensure loopback has 127.0.0.1/8 and ::1/128 independently of network-online.target,
with IPv6 enabled and local hostname/localhost resolution independent of DNS.
Inspect the actual network manager before choosing configuration. Do not disconnect
any physical interface, restart NetworkManager, or reboot the SSH device. Offline
acceptance testing is deferred to the user's separate device.

- [x] Record offline/manual-update and no-disconnection rules in AGENTS.md and lessons.
- [x] Write a loopback/local resolution regression check before implementing network configuration.
- [x] Implement manual scheduling defaults and the bootstrap/setup entrypoints.
- [x] Implement persistent offline loopback configuration without disturbing SSH connectivity.
- [x] Run controlled tests, apply configuration on ipaw.local, verify connected IPv4/IPv6 loopback and idempotency.
- [x] Document the one-command installation and disconnected checks for the user, then commit and push.

### Offline setup review

Published implementation: 61baed1. The exact GitHub curl command installed missing
Git prerequisites, cloned the repository, and ran setup successfully. Repeating it
preserved configuration hashes and the clean checkout. All 78 tests pass on both
development host and Pi; connected IPv4/IPv6 TCP, hostname checks, systemd unit
validation and cloud-init template rendering pass. Update timers are disabled.
The loopback unit is enabled for next boot but was not started. NetworkManager PID,
boot ID and Wi-Fi connectivity remained unchanged. No disconnected/namespace tests,
network reloads, or reboots were performed; offline acceptance is deferred to the
user's separate device as requested.
