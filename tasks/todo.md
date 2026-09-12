# Automated host updates

## Plan and specification

- [x] Inspect repository, device OS, architecture, repositories, disk space, and timers.
- [x] Review upstream Debian and Raspberry Pi upgrade guidance and check the plan against device configuration.
- [x] Build an idempotent installer and unattended updater with a daily systemd timer.
- [x] Verify source migration, release guards, recovery, and noninteractive commands with automated tests.
- [x] Install on ipaw.local, execute a real package update, verify reboot handling and repeat-install behavior.
- [ ] Record applied changes and verification limits; commit and push the finished repository.

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

Defaults: run 10 minutes after boot and daily at 03:00 device-local time, each with up to 30 minutes jitter; automatic
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
- [ ] Commit and push the verified scripts and application record.
