# Trail camera setup

Host setup for minimal Debian on the 64-bit Raspberry Pi. Run scripts on the host.

## Install everything

Connect the camera to the Internet for installation, then run:

```sh
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/conserve-nature/camera-setup/main/install.sh)"
```

The installer uses sudo when necessary, installs missing prerequisites (including
Git), clones the official repository into `/opt/trail-camera/camera-setup`, and
runs `setup.sh`. An existing checkout is updated only by a fast-forward on `main`;
local changes, a different remote/branch, or divergent history stop installation
without discarding work. Initial installation requires a Debian-based 64-bit host,
working Internet access, curl, and root or sudo access.

To reapply the downloaded configuration later, including while offline:

```sh
sudo /opt/trail-camera/camera-setup/setup.sh
```

`setup.sh` runs the numbered setup scripts in order. It does not start an OS update,
reboot, or restart the network manager. Boot-time configuration takes full effect
on the next operator-initiated reboot. Camera/OS changes can leave a reboot pending;
choose an appropriate time to reboot locally. Keep the repository files together
when copying setup to another device.

## Manual packages and OS upgrades

Cameras normally operate offline. Setup installs the update service and an
**optional disabled timer**. It also disables Debian's `apt-daily.timer` and
`apt-daily-upgrade.timer`, including an already-enabled trail-camera timer from
previous versions. Every setup run restores these manual scheduling defaults.
Existing running package transactions are allowed to finish.
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

Once explicitly started, the service handles both package updates and major
release upgrades without prompts. The updater fully upgrades the current release, preserving local configuration files.
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
when the operator next starts maintenance (or at the next optional timer run). Routine current-release updates happen before this check.
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
transition can proceed. These settings control an invoked maintenance run; they do
not schedule one. When an update reboots partway through a major transition, start
the service again after reconnecting to continue or confirm completion.

For a camera with constant connectivity, opt into scheduling explicitly:

```sh
sudo systemctl enable --now trail-camera-update.timer
```

The optional timer runs daily at 03:00–03:30 device-local time and 10–40 minutes
after boot. Enabling it can immediately run overdue maintenance. Disable it again with:

```sh
sudo systemctl disable --now trail-camera-update.timer
```

Stopping the service or shutting down normally waits for the active update to finish.
A forced power loss can still interrupt dpkg; do not interrupt power deliberately.

## Offline IPv4 and IPv6

`02-setup-network.sh` configures loopback independently of external connectivity.
The boot service brings `lo` up with `127.0.0.1/8` and `::1/128`, and persistent
sysctl settings keep IPv6 available on loopback and new interfaces. NetworkManager
is configured to leave loopback unmanaged on its next normal startup. Physical
interfaces and their Wi-Fi, DHCP, routing, firewall, and DNS settings are preserved.

Local hosts mappings supply `localhost` and this machine's hostname without an
external DNS server. Existing unrelated hosts entries are preserved. Cloud-init's
Debian hosts template is kept consistent too, so regenerating `/etc/hosts` preserves
loopback mappings. `.local` discovery between devices still requires a functioning
network; local applications should use `localhost`, `127.0.0.1`, or `::1`.

Setup writes configuration and enables the boot service. It does **not** restart
NetworkManager, change physical interfaces, apply global sysctl changes live, or
perform disconnected tests. If the kernel was booted with `ipv6.disable=1`, remove
that option from the boot command line and reboot at a suitable time before IPv6
can work; setup reports this prerequisite.

Application caveat: glibc's `AI_ADDRCONFIG` lookup flag ignores loopback when deciding
whether an address family is configured. A program using that flag can suppress IPv6
results offline even when `::1` works. Local applications should pass explicit
`getaddrinfo` hints with flags `0`, or use numeric loopback sockets. See the
[getaddrinfo manual](https://www.man7.org/linux/man-pages/man3/getaddrinfo.3.html).
Docker containers have their own network namespaces; this configures the host, not
container IPv6 networks or access from a container to host services.

On a separate device with local-console access, reboot into this configuration,
disconnect its external network, then run:

```sh
python3 /opt/trail-camera/camera-setup/tests/loopback-smoke.py
systemctl status trail-camera-loopback.service --no-pager
ip -brief address show lo
```

The smoke test performs local IPv4/IPv6 TCP round trips and checks local name
resolution. It does not change network configuration. Disconnected operation has
not been tested on `ipaw.local`, per the user's instruction.

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
bash -n install.sh setup.sh 01-setup-updates.sh 02-setup-network.sh
# On the host, without changing or disconnecting its network:
python3 tests/loopback-smoke.py
# On the camera: check enumeration and capture a still, discarding image data.
bash tests/camera-smoke.sh
```

See [device application record](tasks/device-ipaw.md) and [task plan](tasks/todo.md)
for what was actually applied and tested.
