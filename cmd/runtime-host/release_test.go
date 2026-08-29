package main

import (
	"crypto/ed25519"
	"crypto/rand"
	"crypto/sha256"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"os"
	"path/filepath"
	"runtime"
	"testing"
)

func writeTestManifest(t *testing.T, root string, entryPath string, content []byte) {
	t.Helper()
	path := filepath.Join(root, filepath.FromSlash(entryPath))
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(path, content, 0o644); err != nil {
		t.Fatal(err)
	}
	digest := sha256.Sum256(content)
	manifest := releaseManifest{
		SchemaVersion: 1,
		Product:       "LocalDramaStudio",
		Version:       "1.2.3",
		BuildID:       "test-build",
		Channel:       "development",
		Files: []manifestFile{{
			Path: entryPath, Size: int64(len(content)), SHA256: hex.EncodeToString(digest[:]),
		}},
	}
	manifest.Platform.OS, manifest.Platform.Arch = runtime.GOOS, runtime.GOARCH
	manifest.Host.Protocol, manifest.Host.MinimumVersion = 1, "0.1.0"
	manifest.ConfigSchema.Minimum, manifest.ConfigSchema.Target = 1, configSchemaSupported
	raw, err := json.Marshal(manifest)
	if err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(root, "release-manifest.json"), raw, 0o644); err != nil {
		t.Fatal(err)
	}
	identity := releaseIdentity{
		Version:              "1.2.3",
		Channel:              "development",
		HostProtocol:         1,
		WorkerProtocol:       1,
		ConfigSchema:         configSchemaSupported,
		ProjectPackageSchema: 2,
	}
	identityRaw, err := json.Marshal(identity)
	if err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(root, "version.json"), identityRaw, 0o644); err != nil {
		t.Fatal(err)
	}
}

func TestVerifyReleaseAcceptsCompletePayload(t *testing.T) {
	root := t.TempDir()
	writeTestManifest(t, root, "app/module.py", []byte("pass\n"))
	manifest, err := verifyRelease(root)
	if err != nil {
		t.Fatal(err)
	}
	if manifest.Version != "1.2.3" {
		t.Fatalf("unexpected version %q", manifest.Version)
	}
}

func TestVerifyReleaseRejectsTamperedPayload(t *testing.T) {
	root := t.TempDir()
	writeTestManifest(t, root, "app/module.py", []byte("pass\n"))
	if err := os.WriteFile(filepath.Join(root, "app", "module.py"), []byte("tampered\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	if _, err := verifyRelease(root); err == nil {
		t.Fatal("tampered release unexpectedly passed verification")
	}
}

func TestActivateAndRollbackPointer(t *testing.T) {
	root := t.TempDir()
	if err := activateRelease(root, "1.0.0"); err != nil {
		t.Fatal(err)
	}
	if err := activateRelease(root, "1.1.0"); err != nil {
		t.Fatal(err)
	}
	active, err := readActiveRelease(root)
	if err != nil {
		t.Fatal(err)
	}
	if active.Active != "1.1.0" || active.Previous != "1.0.0" || active.Generation != 2 {
		t.Fatalf("unexpected active release: %+v", active)
	}
}

func TestSafeArchiveTargetRejectsTraversal(t *testing.T) {
	root := t.TempDir()
	for _, path := range []string{"../escape", "payload/../../escape", "/absolute"} {
		if _, err := safeArchiveTarget(root, path); err == nil {
			t.Fatalf("unsafe path %q was accepted", path)
		}
	}
	target, err := safeArchiveTarget(root, "package/payload/version.json")
	if err != nil {
		t.Fatal(err)
	}
	if !filepath.IsAbs(target) {
		t.Fatalf("expected absolute target, got %s", target)
	}
}

func TestVerifyReleaseRequiresAndAcceptsTrustedSignature(t *testing.T) {
	root := t.TempDir()
	writeTestManifest(t, root, "app/module.py", []byte("pass\n"))
	manifestPath := filepath.Join(root, "release-manifest.json")
	raw, err := os.ReadFile(manifestPath)
	if err != nil {
		t.Fatal(err)
	}
	var manifest map[string]any
	if err := json.Unmarshal(raw, &manifest); err != nil {
		t.Fatal(err)
	}
	manifest["channel"] = "stable"
	manifest["signature"] = nil
	canonical, _ := json.Marshal(manifest)
	publicKey, privateKey, err := ed25519.GenerateKey(rand.Reader)
	if err != nil {
		t.Fatal(err)
	}
	signature := ed25519.Sign(privateKey, canonical)
	keyDigest := sha256.Sum256(publicKey)
	manifest["signature"] = map[string]any{
		"algorithm": "Ed25519",
		"key_id":    hex.EncodeToString(keyDigest[:8]),
		"value":     base64.StdEncoding.EncodeToString(signature),
	}
	signed, _ := json.Marshal(manifest)
	if err := os.WriteFile(manifestPath, signed, 0o644); err != nil {
		t.Fatal(err)
	}
	previous := trustedReleasePublicKey
	trustedReleasePublicKey = base64.StdEncoding.EncodeToString(publicKey)
	t.Cleanup(func() { trustedReleasePublicKey = previous })
	if _, err := verifyRelease(root); err != nil {
		t.Fatal(err)
	}
}

func TestAcquireHostLockRecoversDeadOwner(t *testing.T) {
	path := filepath.Join(t.TempDir(), "host.lock")
	if err := os.WriteFile(path, []byte(`{"pid":2147483647}`), 0o600); err != nil {
		t.Fatal(err)
	}
	lock, err := acquireHostLock(path)
	if err != nil {
		t.Fatal(err)
	}
	lock.Close()
}

func TestCompareVersions(t *testing.T) {
	if compareVersions("1.2.0", "1.1.9") <= 0 || compareVersions("1.2.0", "1.2.0") != 0 || compareVersions("1.1.9", "1.2.0") >= 0 {
		t.Fatal("semantic version comparison is incorrect")
	}
}
