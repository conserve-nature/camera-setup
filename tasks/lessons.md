# Project lessons

- The trail camera normally operates offline. Automating update steps does not mean
  scheduling them: default to operator-triggered maintenance, with timers opt-in.
- Never disconnect the SSH test device or restart its network manager to test
  offline behavior. Verify configuration and connected loopback locally; the user
  will test disconnected operation on another device.
- Setup must not start an OS upgrade or reboot before all configuration is applied.
