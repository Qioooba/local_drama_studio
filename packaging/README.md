# Release packaging

`packaging/` is the only production packaging authority. Repository scripts under `scripts/dev/` are development tools and are not copied into a release.

## Release contents

Each platform package contains:

- `host/`: the native Go Runtime Host;
- `payload/runtime/python/`: a private relocatable Python 3.12 runtime;
- `payload/app/`: locked production dependencies and precompiled application bytecode;
- `payload/web/`: the production React build;
- `payload/migrations/`, `payload/contracts/`, `payload/version.json`;
- `payload/release-manifest.json`: per-file SHA-256 hashes, compatibility metadata and optional Ed25519 signature;
- `active-release.json`: the atomic active/previous release pointer.

Node.js, pnpm, Go, a global Python installation and source checkout are build-time dependencies only. They are not runtime prerequisites.

## Windows build

Use an official CPython 3.12 embedded archive and prepare it once:

```powershell
packaging/windows/prepare_embedded_python.ps1 `
  -EmbeddedPythonZip C:\release-inputs\python-3.12.x-embed-amd64.zip `
  -Output C:\release-inputs\python-runtime

packaging/windows/prepare_wheelhouse.ps1 -Output C:\release-inputs\wheelhouse

packaging/windows/build.ps1 `
  -PythonRuntime C:\release-inputs\python-runtime `
  -FFmpegRuntime C:\release-inputs\ffmpeg\bin `
  -Wheelhouse C:\release-inputs\wheelhouse `
  -Output C:\release-output
```

The default build is offline after the wheelhouse is prepared. It produces a portable ZIP, `.ldsupdate`, release directory, and—when Inno Setup is installed—an installer. Use `-SkipInstaller` for portable-only builds.

The Windows installer owns the complete runtime-profile lifecycle. Desktop mode uses loopback and removes any Host-owned LAN firewall/service state. Trusted LAN server mode atomically applies the Server template, preserves user-owned machine settings across upgrades, creates a firewall rule for the effective configured port and persisted trusted-network scope, and installs the delayed-auto-start service. Existing installs are migrated by the candidate release before profile fields are merged; first installs bootstrap the selected profile. The native Host commands are idempotent so repair installs do not duplicate rules or discard configuration.

For a non-development channel, provide an Ed25519 private/public key pair. Create a pair from `cmd/release-sign` and keep the private key outside the repository:

```powershell
go run ./cmd/release-sign keygen --private C:\secure\release.key --public C:\secure\release.pub
```

Then pass `-SigningPrivateKey`, `-SigningPublicKey`, and the Windows certificate-store `-CodeSigningCertificateThumbprint`. The public key is embedded into the Host; the private key signs the manifest and is never copied into the artifact. Host, desktop launcher and installer receive SHA-256 Authenticode signatures with an RFC 3161 timestamp.

## Linux build

Build on Linux amd64 with Python 3.12, Go, Node.js and pnpm available:

```sh
python3 packaging/common/prepare_wheelhouse.py /srv/release-inputs/wheelhouse
packaging/linux/build.sh /srv/release-inputs/python-runtime /srv/release-inputs/wheelhouse /srv/release-output
```

Set `SIGNING_PRIVATE_KEY` and `SIGNING_PUBLIC_KEY` for signed non-development releases.

## Reproducibility controls

- Python production dependencies are exact-version locked and an offline wheelhouse hash manifest is verified before installation.
- Application and dependency bytecode uses checked-hash invalidation.
- Go uses `-trimpath`; release identity is injected at link time.
- pnpm uses `--frozen-lockfile`.
- non-development builds reject a dirty Git worktree;
- final release manifests hash every payload file.
