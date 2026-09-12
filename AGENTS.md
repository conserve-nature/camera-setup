# Camera setup constraints

- This Raspberry Pi AI trail camera normally operates offline.
- IPv4, IPv6, loopback, and local hostname resolution must not depend on external connectivity.
- OS updates are operator-triggered by default. Optional systemd timers must be disabled by default.
- The bootstrap installs prerequisites, clones this repository, and runs setup without starting OS upgrades or rebooting.
- Never disconnect the SSH test device or restart its network manager for verification. The user tests disconnected operation on another device.
- Keep setup idempotent. Record applied device changes and validation limits in tasks/device-ipaw.md.
- Never store device credentials in this repository.
