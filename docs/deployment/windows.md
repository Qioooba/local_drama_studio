# Windows deployment guide

## Supported shapes

The Windows artifact supports two profiles with the same code and data contract:

| Profile | Bind | Lifecycle | Native interaction |
|---|---|---|---|
| `DESKTOP` | loopback by default | user-launched Runtime Host | file picker, Credential Manager, SAPI |
| `SERVER` | configured LAN interface | Windows SCM service | browser upload/download; no server desktop dialog |

The installer copies immutable releases to `%ProgramFiles%\LocalDramaStudio\versions\<version>`. Mutable state is external and survives uninstall:

```text
%ProgramData%\LocalDramaStudio\
  config\config.json
  data\local_drama.sqlite3
  projects\
  work\
  cache\
  backups\
  logs\
  runtime\
  updates\sessions\
```

Do not put the production database or projects under `Program Files` or inside a portable extraction directory.

## Installation

1. Run the signed installer as administrator.
2. Select desktop mode or the Windows service task.
3. Keep the generated config unless the bind address, allowed origins, model roots or tool paths must change.
4. For server mode, restrict TCP 3210 to the trusted subnet with Windows Firewall before exposing the bind address.
5. Run diagnostics:

```powershell
"C:\Program Files\LocalDramaStudio\host\local-drama-host.exe" doctor
"C:\Program Files\LocalDramaStudio\host\local-drama-host.exe" status
```

The Host performs config migration, database backup/rehearsal/migration, API readiness, and Worker launch. It is the only production lifecycle authority.

## Service identity and secrets

Windows Credential Manager is scoped to the account running the API. A service and an interactive desktop process do not automatically share secrets. Configure provider credentials through the application while it runs under its final service identity, or use explicitly provisioned environment-based secrets.

The default service is delayed-auto-start and has three bounded restart attempts configured through SCM. Database and projects remain owned by the instance directory, not by a release.

## Portable use

Extract the portable ZIP, set `LOCAL_DRAMA_INSTANCE_ROOT` to a durable writable directory, and run `host\local-drama-host.exe run`. Portable mode is not permission-free: the selected instance directory still needs appropriate ACLs and backups.

## Development compatibility command

`scripts/start.ps1` now builds and delegates to the same Runtime Host. `scripts/dev/start_api.ps1` and `scripts/dev/start_web.ps1` are intentionally separate developer-only entrypoints and are never production authorities.
