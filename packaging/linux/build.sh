#!/usr/bin/env sh
set -eu

if [ "$#" -lt 1 ]; then
  echo "usage: $0 PYTHON_RUNTIME [WHEELHOUSE] [OUTPUT]" >&2
  exit 2
fi

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
REPOSITORY_ROOT=$(CDPATH= cd -- "$SCRIPT_DIR/../.." && pwd)
PYTHON_RUNTIME=$1
WHEELHOUSE=${2:-}
OUTPUT=${3:-"$REPOSITORY_ROOT/dist"}
VERSION=$(python3 -c "import json; print(json.load(open('$REPOSITORY_ROOT/release/version.json', encoding='utf-8'))['version'])")
CHANNEL=$(python3 -c "import json; print(json.load(open('$REPOSITORY_ROOT/release/version.json', encoding='utf-8'))['channel'])")
SIGNING_PRIVATE_KEY=${SIGNING_PRIVATE_KEY:-}
SIGNING_PUBLIC_KEY=${SIGNING_PUBLIC_KEY:-}
if [ "$CHANNEL" != "development" ] && { [ -z "$SIGNING_PRIVATE_KEY" ] || [ -z "$SIGNING_PUBLIC_KEY" ]; }; then
  echo "Non-development releases require SIGNING_PRIVATE_KEY and SIGNING_PUBLIC_KEY" >&2
  exit 1
fi
TRUSTED_PUBLIC_KEY=""
if [ -n "$SIGNING_PUBLIC_KEY" ]; then
  TRUSTED_PUBLIC_KEY=$(tr -d '\r\n ' < "$SIGNING_PUBLIC_KEY")
fi

cd "$REPOSITORY_ROOT/apps/web"
pnpm install --frozen-lockfile
pnpm run build

mkdir -p "$OUTPUT/build"
cd "$REPOSITORY_ROOT/cmd/runtime-host"
go test ./...
CGO_ENABLED=0 GOOS=linux GOARCH=amd64 go build -trimpath -ldflags "-s -w -X main.hostVersion=$VERSION -X main.trustedReleasePublicKey=$TRUSTED_PUBLIC_KEY" -o "$OUTPUT/build/local-drama-host" .

set -- python3 "$REPOSITORY_ROOT/packaging/common/build_release.py" \
  --platform linux-amd64 \
  --python-runtime "$PYTHON_RUNTIME" \
  --host "$OUTPUT/build/local-drama-host" \
  --output "$OUTPUT"
if [ -n "$WHEELHOUSE" ]; then
  set -- "$@" --offline --wheelhouse "$WHEELHOUSE"
fi
"$@"

if [ -n "$SIGNING_PRIVATE_KEY" ]; then
  cd "$REPOSITORY_ROOT/cmd/release-sign"
  go run . sign --manifest "$OUTPUT/LocalDramaStudio-$VERSION-linux-amd64/payload/release-manifest.json" --private "$SIGNING_PRIVATE_KEY"
fi

cp "$SCRIPT_DIR/local-drama-studio.service" "$OUTPUT/LocalDramaStudio-$VERSION-linux-amd64/"
cp "$SCRIPT_DIR/config.server.json" "$OUTPUT/LocalDramaStudio-$VERSION-linux-amd64/"
cp "$SCRIPT_DIR/install.sh" "$OUTPUT/LocalDramaStudio-$VERSION-linux-amd64/"
cp "$SCRIPT_DIR/uninstall.sh" "$OUTPUT/LocalDramaStudio-$VERSION-linux-amd64/"
chmod 0755 "$OUTPUT/LocalDramaStudio-$VERSION-linux-amd64/host/local-drama-host"
chmod 0755 "$OUTPUT/LocalDramaStudio-$VERSION-linux-amd64/install.sh"
chmod 0755 "$OUTPUT/LocalDramaStudio-$VERSION-linux-amd64/uninstall.sh"
tar -C "$OUTPUT" -czf "$OUTPUT/LocalDramaStudio-$VERSION-linux-amd64.tar.gz" "LocalDramaStudio-$VERSION-linux-amd64"
cp "$OUTPUT/LocalDramaStudio-$VERSION-linux-amd64.tar.gz" "$OUTPUT/LocalDramaStudio-$VERSION-linux-amd64.ldsupdate"
(cd "$OUTPUT" && sha256sum "LocalDramaStudio-$VERSION-linux-amd64.tar.gz" "LocalDramaStudio-$VERSION-linux-amd64.ldsupdate" > checksums.txt)
