# Trail camera setup

Host setup for minimal Debian on the 64-bit Raspberry Pi. Run scripts on the host.

## Automated packages and OS upgrades

Copy this directory to the camera and run:

```sh
sudo ./01-setup-updates.sh
```

This idempotently installs the updater and a systemd timer. By default it runs daily
at **03:00–03:30 in the device's local timezone**, and **10–40 minutes after boot**,
including a catch-up run after missed maintenance. Installation enables the timer;
an overdue boot or daily run can start immediately, but installation does not wait for it.
APT's two default timers are disabled to give this job ownership of scheduling.
Existing running APT services are allowed to finish.
The installer enables persistent systemd logging capped at 64 MiB / 14 days.

On Raspberry Pi hardware, setup explicitly selects this project's **IMX500 AI
camera** in a managed boot configuration block (`camera_auto_detect=0`,
`dtoverlay=imx500`) and preserves the original configuration backup. This avoids a
[known IMX500 firmware autodetection regression](https://github.com/raspberrypi/firmware/commit/ae2a7dc5330b7ea2c7107e5c4cb6b2691355bb9c).
It is intended for this fixed camera configuration; review it before using another
sensor or custom camera overlay. A changed boot configuration queues a reboot through
the updater. Both the helper and the installer must be present when running setup.

```sh
# Preview with temporary APT indexes; no package installation, source rewrite or reboot.
sudo /usr/local/lib/trail-camera/update-os.py --check

# Run independently of the SSH connection. It may reboot the device.
sudo systemctl start --no-block trail-camera-update.service

# Inspect progress and schedule.
journalctl -u trail-camera-update.service -n 100 --no-pager
systemctl list-timers trail-camera-update.timer
```

Both package updates and major release upgrades are enabled automatically. The
updater fully upgrades the current release, preserving local configuration files.
Package scripts may restart services; `needrestart` only reports stale services to
avoid restarting the updater from inside its own APT transaction. If packages changed, it reboots;
this deliberately covers Pi kernel/firmware packages that may not create Debian's
`reboot-required` flag. A run with nothing changed does not reboot. It does not run
`rpi-update`, update container images, or automatically remove orphaned packages.
Old kernels/cached packages may need cleanup if storage eventually becomes tight.

After the current release is updated and booted, the next maintenance run discovers
Debian stable using signed release metadata and automatically attempts an **adjacent
major release**. It verifies target repository signatures, codename and architecture,
simulates dependency resolution, backs up host configuration/package metadata, then
updates APT suites and performs minimal and full upgrades. If a vendor has not yet
published matching repositories, the run fails before altering sources and retries
at the next daily run. Routine current-release updates happen before this check.
The subsequent run confirms the completed major upgrade has been rebooted.

Supported source layouts: conventional `.list` and deb822 `.sources`, pinned to
the installed codename (`trixie`, `trixie-updates`, `trixie-security`, or
`trixie-backports`). Supported archives: `deb.debian.org`, `security.debian.org`,
`archive.raspberrypi.com` / `.org`, and Docker's `download.docker.com/linux/debian`.
Disabled source entries remain unchanged. Unknown archives, floating release names,
insecure source options, held packages (for major upgrades), custom APT pins (for
major upgrades), and skipped major versions stop automation with a logged error.
Review/extend the script before introducing another archive. 32-bit Raspbian is not
supported. Runtime dependencies are Python 3, gpgv, Debian archive keys, CA
certificates, APT, dpkg, and systemd; these are present on the test image.

Change defaults in `/etc/trail-camera-updates.json` (reinstallation preserves it):

```json
{
  "automatic_major": true,
  "automatic_reboot": true
}
```

Setting `automatic_reboot` to false requires a manual reboot before a pending major
transition can proceed. Stop future scheduling with:

```sh
sudo systemctl disable --now trail-camera-update.timer
```

Stopping the service or shutting down normally waits for the active update to finish.
A forced power loss can still interrupt dpkg; do not interrupt power deliberately.

## Recovery and records

`/var/lib/trail-camera-updates/` is root-only and holds:

- `last-success.json`: last successful regular package update.
- `package-baseline.json`: package fingerprint retained across interrupted updates.
- `reboot-pending.json`: outstanding reboot and the boot that requested it.
- `transaction.json`: target source files and resumable major-upgrade state.
- `last-major-upgrade.json`: most recent major upgrade confirmed after reboot.
- `backup-*/host-state.tar.gz` and `packages.txt`: pre-major `/etc`, dpkg state,
  APT extended state and package selections. Backups are retained for manual recovery.

These backups are **not a bootable image or automatic rollback**. If packages have
already been upgraded, restoring old APT sources is not a valid rollback. The updater
retains target sources and retries dependency repair and the upgrade. If it fails,
inspect the journal and `dpkg --audit`, repair the reported issue, then rerun the
service. Hardware failures or loss of networking can require local access or reimaging.
APT/dpkg also keep their normal logs in `/var/log/apt/` and `/var/log/dpkg.log`.

Docker applications still rely on host networking, kernel, firmware and camera
interfaces. Raspberry Pi [recommends a clean install for major releases](https://www.raspberrypi.com/documentation/computers/os.html#upgrade-to-a-new-major-version),
so this is a deliberate best-effort unattended in-place policy, not an officially
supported Raspberry Pi migration. Future release-specific changes cannot be verified
in advance. Debian documents [sequential upgrades, preparation and recovery](https://www.debian.org/releases/trixie/release-notes/upgrading.html).

## Verification

```sh
python3 -m unittest discover -s tests -v
bash -n 01-setup-updates.sh
# On the camera: check enumeration and capture a still, discarding image data.
bash tests/camera-smoke.sh
```

See [device application record](tasks/device-ipaw.md) and [task plan](tasks/todo.md)
for what was actually applied and tested.
