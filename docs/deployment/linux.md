# Linux deployment guide

Linux uses the same Python application, web build, migrations, config schema, database layout, release manifest and update state machine as Windows. Only the Host binary and platform adapters differ.

## Filesystem layout

```text
/opt/local-drama-studio/             immutable host and versions
/etc/local-drama-studio/config.json  machine config
/var/lib/local-drama-studio/         database, projects, work, backups, secrets and update sessions
```

Run the extracted package installer as root:

```sh
sudo ./install.sh
systemctl status local-drama-studio
journalctl -u local-drama-studio -f
```

The installer creates the non-login `localdrama` service account, installs the hardened systemd unit, verifies the release, creates a recovery set, migrates the database, smoke-tests the candidate and then activates it.

The supplied server config binds loopback. Put a maintained reverse proxy in front for TLS, authentication and LAN/WAN access. If binding the application directly to a LAN address, set `network.mode` to `LAN_SERVICE`, configure explicit allowed origins, and enforce firewall policy first.

## Linux capability behavior

- browser upload/download replaces a server-native file dialog;
- secrets use service-owned mode-0600 files under the instance directory unless another adapter is configured;
- Windows SAPI is reported unavailable; TTS work is rejected with a stable capability error rather than crashing the service;
- FFmpeg, FFprobe, ComfyUI and model paths are machine configuration, never source-tree constants.

## Operations

```sh
/opt/local-drama-studio/host/local-drama-host doctor
/opt/local-drama-studio/host/local-drama-host status
sudo systemctl restart local-drama-studio
sudo -u localdrama /opt/local-drama-studio/host/local-drama-host verify-release
```

`uninstall.sh` removes the systemd unit and `/opt` binaries but intentionally preserves `/etc/local-drama-studio` and `/var/lib/local-drama-studio`.

Release activation under `/opt` is an administrative operation. Keep the service account unable to rewrite application binaries; use the root-owned upgrade sequence in the recovery runbook and return instance ownership to `localdrama` before starting the service.
