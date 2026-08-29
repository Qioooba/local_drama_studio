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
  models\
    downloads\
    staging\
    quarantine\
    libraries\
      comfyui\
      pytorch\
      ollama\
      audio\
```

Do not put the production database or projects under `Program Files` or inside a portable extraction directory.

## Path contract

- `${INSTANCE_ROOT}` and `${RELEASE_ROOT}` are expanded by the server. `runtime.model_root` is then resolved, and `${MODEL_ROOT}` may be used by runtime fields and library bindings. Relative paths in `config.json` are anchored to `${INSTANCE_ROOT}`, never to the Windows service working directory.
- `data`, `projects`, `work`, `cache`, `backups` and `logs` are independent mutable roots. Database records store only POSIX-style root-relative keys such as `04_media/videos/...`; moving an instance does not write old drive letters into project media records.
- `runtime.model_root` is machine configuration, not model identity. V2 model records use a ModelLibrary and root-relative artifact paths; they never return server absolute paths to the browser. `runtime.model_library_roots` contains only directories eligible for discovery. `downloads`, `staging`, and `quarantine` are operational areas, never scanner roots and never automatically promoted to a model.
- A remote browser uploads bytes and downloads through `/api/...` URLs. It never submits a path from its own computer, opens a server desktop dialog, or interprets `D:\...` as a client path.
- Browser-supplied filenames are normalized for Windows (including reserved names such as `CON`, invalid characters, path fragments and trailing dots/spaces) before a temporary or project file is created.

The complete invariant matrix and migration checklist are in [Windows server path contract](windows-path-contract.md).
The release procedure and V2 ModelRoot/Adapter acceptance gates are in [Windows Server V2 model platform runbook](../release/windows-server-v2-model-platform-runbook.md).

### ModelRoot and model libraries

Schema v2 creates the following default ModelRoot under `%ProgramData%\LocalDramaStudio\models`. The root can instead point to a dedicated local data volume, such as `E:\LocalDramaModels`.

```json
{
  "schema_version": 2,
  "runtime": {
    "model_root": "${INSTANCE_ROOT}/models",
    "model_library_roots": [
      "${MODEL_ROOT}/libraries/comfyui",
      "${MODEL_ROOT}/libraries/pytorch",
      "${MODEL_ROOT}/libraries/ollama",
      "${MODEL_ROOT}/libraries/audio"
    ]
  }
}
```

To move the *storage policy* to an already prepared local volume, stop the Host and run the single machine-level command:

```powershell
& "C:\Program Files\LocalDramaStudio\host\local-drama-host.exe" stop
# Wait until `local-drama-host.exe status` reports `STOPPED`.
& "C:\Program Files\LocalDramaStudio\host\local-drama-host.exe" configure-model-root --path "E:\LocalDramaModels"
```

The command requires config schema v2, writes an atomic config backup, creates the canonical directories, and refuses to run while API/Worker is live. It does **not** move, copy, delete, or silently re-register models; it also preserves administrator-defined `model_library_roots`. Copy or move data deliberately, update any custom library binding if appropriate, then run the V2 Model Center discovery and validation workflow. UNC shares are rejected for this machine-owned GPU model root.

## Installation

1. Run the signed installer as administrator.
2. Select desktop mode or the Windows service task.
3. Select **Trusted LAN server** to install the delayed-auto-start Windows service. The installer atomically merges the Server profile into the existing machine config, preserving custom storage, runtime, port and Origin values.
4. On reinstall or upgrade, the candidate release migrates the existing machine config and creates config/database recovery points before profile defaults are merged. Schema v2 adds `runtime.model_root` and the four discovery library roots; the migration changes configuration only and never moves or deletes existing model files. A first install bootstraps the selected profile before maintenance. The installer never copies a template directly over the machine config.
5. The installer selects Server mode by default. It creates the `LocalDramaStudio LAN` inbound rule for the configured TCP port and the persisted `network.firewall_remote_address` scope (`LocalSubnet` by default), with edge traversal disabled. Narrow it with `local-drama-host.exe configure-firewall --remote-address <CIDR>`; the Host backs up the config and persists that scope, so a repair or later upgrade will not broaden it. Clear the installer option to use Desktop mode.
6. Run diagnostics:

```powershell
"C:\Program Files\LocalDramaStudio\host\local-drama-host.exe" doctor
"C:\Program Files\LocalDramaStudio\host\local-drama-host.exe" status
```

The Host performs config migration, database backup/rehearsal/migration, API readiness, and Worker launch. It is the only production lifecycle authority.

Switching a later installer run back to Desktop changes the bind policy to loopback, removes the LAN firewall rule, and uninstalls the service. Uninstall also removes the Host-owned firewall rule and service while preserving configuration, projects, database, logs and backups.

### Reinstall and upgrade ownership

| Category | Reinstall / upgrade behavior |
| --- | --- |
| Application binaries, bundled Python, web assets, FFmpeg and config templates | Replaced by the newly verified release. Old release payload remains available to the Host rollback flow. |
| `install_profile`, `network.mode`, bind `host`, trusted-LAN acknowledgement | Set from the runtime mode selected in the installer. These fields change together so Desktop and Server policies cannot be mixed. |
| Port, allowed Origins and firewall remote scope | Preserved. A missing setting receives the new template default; an administrator's existing value wins. |
| Storage roots, ModelRoot, model-library roots, runtime endpoints, worker channels and tool overrides | Preserved. Schema v2 adds an instance-relative ModelRoot and its canonical scanner-library defaults only when no library binding exists; no existing model files are moved or deleted. Unsupported keys remain subject to strict schema validation rather than being silently accepted. |
| Projects, media, SQLite database, logs and backups under `%ProgramData%\LocalDramaStudio` | Never copied over or removed by install, repair, upgrade or normal uninstall. |
| Database and config schema | Migrated by the candidate release after recovery points are created. A failed candidate restores the pre-upgrade config/database and does not activate the release. |
| Windows service and Host-owned firewall rule | Reconciled idempotently to the selected mode. Existing service identity and unrelated service settings are retained; Desktop mode removes the Host-owned service/rule. |
| Provider secrets | Not stored in the release tree. Windows Credential Manager entries follow the Windows account/service identity and are not overwritten by the installer. |

LAN service mode currently has no application login. Its config therefore requires the explicit `trusted_lan_unauthenticated=true` acceptance flag; both the native Host and API reject LAN startup without it, and the web shell continuously displays the trusted-LAN/no-login state.

## Service identity and secrets

Windows Credential Manager is scoped to the account running the API. A service and an interactive desktop process do not automatically share secrets. Configure provider credentials through the application while it runs under its final service identity, or use explicitly provisioned environment-based secrets.

The default service is delayed-auto-start and has three bounded restart attempts configured through SCM. Database and projects remain owned by the instance directory, not by a release.

## Portable use

Extract the portable ZIP, set `LOCAL_DRAMA_INSTANCE_ROOT` to a durable writable directory, and run `host\local-drama-host.exe run`. Portable mode is not permission-free: the selected instance directory still needs appropriate ACLs and backups.

## Development compatibility command

`scripts/start.ps1` now builds and delegates to the same Runtime Host. `scripts/dev/start_api.ps1` and `scripts/dev/start_web.ps1` are intentionally separate developer-only entrypoints and are never production authorities.
