package main

import (
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func writeTestIdentity(t *testing.T, root string, version string, mutate ...func(*releaseIdentity)) {
	t.Helper()
	identity := releaseIdentity{
		Version:              version,
		Channel:              "development",
		HostProtocol:         1,
		WorkerProtocol:       1,
		ConfigSchema:         1,
		ProjectPackageSchema: 2,
	}
	for _, change := range mutate {
		change(&identity)
	}
	raw, err := json.Marshal(identity)
	if err != nil {
		t.Fatal(err)
	}
	if err := os.MkdirAll(root, 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(root, "version.json"), raw, 0o644); err != nil {
		t.Fatal(err)
	}
}

func setTestMachineEnv(t *testing.T, installRoot string) {
	t.Helper()
	t.Setenv("LOCAL_DRAMA_INSTALL_ROOT", installRoot)
	t.Setenv("LOCAL_DRAMA_INSTANCE_ROOT", installRoot)
	t.Setenv("LOCAL_DRAMA_PYTHON", filepath.Join(installRoot, ".venv", "Scripts", "python.exe"))
	t.Setenv("LOCAL_DRAMA_CONFIG", "")
	t.Setenv("LOCAL_DRAMA_HOST", "")
	t.Setenv("LOCAL_DRAMA_PORT", "")
}

func discoverTestPaths(t *testing.T, installRoot string) hostPaths {
	t.Helper()
	setTestMachineEnv(t, installRoot)
	paths, _, err := discover("")
	if err != nil {
		t.Fatal(err)
	}
	return paths
}

func TestLoadReleaseIdentityPrefersReleaseRoot(t *testing.T) {
	root := t.TempDir()
	writeTestIdentity(t, root, "1.2.3")
	writeTestIdentity(t, filepath.Join(root, "release"), "0.1.0")
	identity, err := loadReleaseIdentity(root, root)
	if err != nil {
		t.Fatal(err)
	}
	if identity.Version != "1.2.3" {
		t.Fatalf("expected release-root identity to win, got %q", identity.Version)
	}
}

func TestLoadReleaseIdentityFallsBackToRepositoryReleaseDir(t *testing.T) {
	root := t.TempDir()
	writeTestIdentity(t, filepath.Join(root, "release"), "0.1.0")
	identity, err := loadReleaseIdentity(root, root)
	if err != nil {
		t.Fatal(err)
	}
	if identity.Version != "0.1.0" || identity.Channel != "development" {
		t.Fatalf("unexpected identity: %+v", identity)
	}
}

func TestLoadReleaseIdentityRejectsMissingFile(t *testing.T) {
	root := t.TempDir()
	if _, err := loadReleaseIdentity(root, root); err == nil {
		t.Fatal("missing identity unexpectedly resolved")
	}
}

func TestLoadReleaseIdentityRejectsInvalidIdentity(t *testing.T) {
	cases := []struct {
		name   string
		mutate func(*releaseIdentity)
	}{
		{"unsafe version", func(identity *releaseIdentity) { identity.Version = ">1.0.0" }},
		{"empty channel", func(identity *releaseIdentity) { identity.Channel = "" }},
		{"unsupported host protocol", func(identity *releaseIdentity) { identity.HostProtocol = 2 }},
		{"unsupported worker protocol", func(identity *releaseIdentity) { identity.WorkerProtocol = 2 }},
		{"zero config schema", func(identity *releaseIdentity) { identity.ConfigSchema = 0 }},
	}
	for _, item := range cases {
		t.Run(item.name, func(t *testing.T) {
			root := t.TempDir()
			writeTestIdentity(t, root, "1.2.3", item.mutate)
			if _, err := loadReleaseIdentity(root, root); err == nil {
				t.Fatal("invalid identity unexpectedly accepted")
			}
		})
	}
}

func TestDiscoverStaysMachineScopedWithoutRelease(t *testing.T) {
	root := t.TempDir()
	setTestMachineEnv(t, root)
	paths, _, err := discover("")
	if err != nil {
		t.Fatal(err)
	}
	if paths.Version != "" || paths.ReleaseRoot != "" || paths.Python != "" {
		t.Fatalf("discover must not resolve release scope: %+v", paths)
	}
	if paths.RuntimeRoot != filepath.Join(root, "runtime") {
		t.Fatalf("unexpected runtime root %q", paths.RuntimeRoot)
	}
}

func TestResolveReleaseDevIdentity(t *testing.T) {
	root := t.TempDir()
	writeTestIdentity(t, filepath.Join(root, "release"), "0.1.0")
	if err := os.MkdirAll(filepath.Join(root, "apps", "api", "local_drama"), 0o755); err != nil {
		t.Fatal(err)
	}
	paths := discoverTestPaths(t, root)
	if err := resolveRelease(&paths); err != nil {
		t.Fatal(err)
	}
	if paths.Version != "0.1.0" {
		t.Fatalf("unexpected dev version %q", paths.Version)
	}
	if paths.ReleaseRoot != root || paths.AppRoot != filepath.Join(root, "apps", "api") {
		t.Fatalf("unexpected dev paths: %+v", paths)
	}
	if paths.HostProtocol != 1 {
		t.Fatalf("unexpected host protocol %d", paths.HostProtocol)
	}
}

func TestResolveReleaseFollowsActivePointer(t *testing.T) {
	root := t.TempDir()
	writeTestIdentity(t, filepath.Join(root, "versions", "1.2.3"), "1.2.3")
	if err := writeActiveRelease(root, activeRelease{SchemaVersion: 1, Active: "1.2.3", Generation: 1}); err != nil {
		t.Fatal(err)
	}
	paths := discoverTestPaths(t, root)
	if err := resolveRelease(&paths); err != nil {
		t.Fatal(err)
	}
	if paths.Version != "1.2.3" || paths.ReleaseRoot != filepath.Join(root, "versions", "1.2.3") {
		t.Fatalf("unexpected packaged paths: %+v", paths)
	}
}

func TestResolveReleaseRejectsActivePointerDrift(t *testing.T) {
	root := t.TempDir()
	writeTestIdentity(t, filepath.Join(root, "versions", "9.9.9"), "1.2.3")
	if err := writeActiveRelease(root, activeRelease{SchemaVersion: 1, Active: "9.9.9", Generation: 1}); err != nil {
		t.Fatal(err)
	}
	paths := discoverTestPaths(t, root)
	err := resolveRelease(&paths)
	if err == nil || !strings.Contains(err.Error(), "does not match") {
		t.Fatalf("expected version drift error, got %v", err)
	}
}

func TestResolveReleaseFailsWithoutIdentity(t *testing.T) {
	root := t.TempDir()
	paths := discoverTestPaths(t, root)
	if err := resolveRelease(&paths); err == nil {
		t.Fatal("resolveRelease unexpectedly succeeded without version identity")
	}
}

func TestResolveReleaseFailsWithoutPython(t *testing.T) {
	root := t.TempDir()
	writeTestIdentity(t, filepath.Join(root, "release"), "0.1.0")
	setTestMachineEnv(t, root)
	t.Setenv("LOCAL_DRAMA_PYTHON", "")
	paths, _, err := discover("")
	if err != nil {
		t.Fatal(err)
	}
	if err := resolveRelease(&paths); err == nil {
		t.Fatal("resolveRelease unexpectedly succeeded without Python")
	}
}

func TestVerifyReleaseRejectsIdentityMismatch(t *testing.T) {
	root := t.TempDir()
	writeTestManifest(t, root, "app/module.py", []byte("pass\n"))
	writeTestIdentity(t, root, "1.2.4")
	if _, err := verifyRelease(root); err == nil {
		t.Fatal("manifest/identity mismatch unexpectedly passed verification")
	}
}

func TestVerifyReleaseRejectsMissingIdentity(t *testing.T) {
	root := t.TempDir()
	writeTestManifest(t, root, "app/module.py", []byte("pass\n"))
	if err := os.Remove(filepath.Join(root, "version.json")); err != nil {
		t.Fatal(err)
	}
	if _, err := verifyRelease(root); err == nil {
		t.Fatal("payload without version identity unexpectedly passed verification")
	}
}

func TestAbsoluteFromAnchorsRelativeServicePaths(t *testing.T) {
	base := t.TempDir()
	resolved, err := absoluteFrom(filepath.Join("instance", "data"), base)
	if err != nil {
		t.Fatal(err)
	}
	want, err := filepath.Abs(filepath.Join(base, "instance", "data"))
	if err != nil {
		t.Fatal(err)
	}
	if resolved != want {
		t.Fatalf("resolved path = %q, want %q", resolved, want)
	}
}

func TestAbsoluteFromRejectsEmptyPath(t *testing.T) {
	if _, err := absoluteFrom("  ", t.TempDir()); err == nil {
		t.Fatal("expected empty path to be rejected")
	}
}
