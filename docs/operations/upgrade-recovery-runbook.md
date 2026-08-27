# Upgrade and recovery runbook

## Apply an update

Stop/start coordination is built into the Host. Import either an extracted release directory or a platform `.ldsupdate` archive:

```powershell
local-drama-host.exe upgrade --bundle D:\updates\LocalDramaStudio-1.2.0-windows-amd64.ldsupdate
```

```sh
sudo systemctl stop local-drama-studio
sudo env LOCAL_DRAMA_INSTALL_ROOT=/opt/local-drama-studio \
  LOCAL_DRAMA_INSTANCE_ROOT=/var/lib/local-drama-studio \
  LOCAL_DRAMA_CONFIG=/etc/local-drama-studio/config.json \
  /opt/local-drama-studio/host/local-drama-host upgrade --bundle /srv/updates/LocalDramaStudio-1.2.0-linux-amd64.ldsupdate
sudo chown -R localdrama:localdrama /var/lib/local-drama-studio
sudo systemctl restart local-drama-studio
```

The command performs these durable stages:

1. stop/drain the current Host;
2. safely extract with traversal, symlink, file-count and expanded-size limits;
3. verify platform, protocol, signature policy and every payload SHA-256;
4. stage an immutable `versions/<version>` directory;
5. back up/migrate config;
6. create a COMPLETE recovery set;
7. back up and rehearse database migrations on a copy;
8. migrate the live database with automatic restore on failure;
9. start the candidate API, require readiness and static-web smoke, and verify Worker compatibility;
10. atomically commit `active-release.json`.

Progress is persisted under `<instance>/updates/sessions/<id>/state.json`. A failed command leaves the active pointer unchanged and records `FAILED` with the recovery-set path.

## Roll back

```powershell
local-drama-host.exe rollback
```

The Host first checks whether the previous application accepts the current database. If not, it restores the matching pre-upgrade recovery database, validates it with the previous release, and only then swaps active/previous. It never runs Alembic downgrade as a substitute for recovery.

Restart the service after a successful rollback.

## Manual maintenance

```powershell
python -m local_drama.entrypoints.maintenance --config C:\ProgramData\LocalDramaStudio\config\config.json inspect
python -m local_drama.entrypoints.maintenance --config C:\ProgramData\LocalDramaStudio\config\config.json recovery-create
python -m local_drama.entrypoints.maintenance --config C:\ProgramData\LocalDramaStudio\config\config.json diagnostics-create
```

Diagnostics bundles contain redacted config, release/migration/platform facts, storage capacity and log inventory. They exclude database contents, project media, scripts and secret values.

## Failure policy

- Never edit `active-release.json` while processes are running.
- Never copy a database file while SQLite is live; use the maintenance online backup path.
- Never delete the last known-good version or COMPLETE recovery set before the retention window expires.
- Preserve failed candidate files, session state and logs until the incident is understood.
- If both active and previous fail validation, stop and restore from a separately tested backup; do not force schema downgrade.
