# Script boundary

Production lifecycle authority is `cmd/runtime-host`. `start.ps1` and `stop.ps1` are compatibility delegates to that Host; direct API/web development commands live under `scripts/dev/`. Packaging never copies repository scripts into a release.

`scripts/` contains developer and operational entrypoints. The web page never loads, creates, or executes these files. Page-configurable behavior belongs to API schemas and application services; Comfy executable graphs belong to immutable Workflow Versions.

The authoritative categories are produced from source facts:

- `RUNTIME_ENTRYPOINT`: starts an API/worker or runs database migration. Parameters come from CLI/environment and `Settings`.
- `VERIFICATION`: UAT, audit, benchmark, browser or diagnostic code. It may use fixture UUIDs, but those values are test evidence and are never product defaults.
- `ARTIFACT_GENERATOR`: generates clients, reports, evidence, subtitles or other files when explicitly invoked by a developer.
- `DEVELOPER_DATA_TOOL`: directly invokes application services to seed/repair local test data. It is not a substitute for page acceptance evidence.
- `DEVELOPER_TOOL`: remaining repository maintenance commands.

Run the boundary gate from the repository root:

```powershell
python scripts/audit_script_boundaries.py --strict
```

The gate rejects machine-specific absolute Windows paths. Repository paths must derive from `Path(__file__)`; external tools must resolve from CLI, environment, `PATH`, or an explicitly documented optional fallback. Use `--output work/reports/script-boundaries.json` only after creating that report directory.

Product workflow lifecycle:

1. `/workflow-definitions` exposes fields and effects used by the page.
2. `:instantiate` compiles a server-owned Comfy graph into an immutable candidate.
3. Comfy Lab captures user-exported API Format JSON in `work/comfy-lab`.
4. A matching PASS execution test is required before `:promote` creates a candidate.
5. Compatibility validation and explicit publish produce the runtime-eligible Workflow Version.

Generated scripts and hardcoded verification fixtures must not be imported by `apps/api/local_drama` or `apps/web/src`.
