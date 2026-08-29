package main

import (
	"encoding/json"
	"os"
	"path/filepath"
	"reflect"
	"strings"
	"testing"
)

func writeJSONFixture(t *testing.T, path string, value map[string]any) []byte {
	t.Helper()
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		t.Fatal(err)
	}
	raw, err := json.MarshalIndent(value, "", "  ")
	if err != nil {
		t.Fatal(err)
	}
	raw = append(raw, '\n')
	if err := os.WriteFile(path, raw, 0o600); err != nil {
		t.Fatal(err)
	}
	return raw
}

func TestApplyProfileTemplatePreservesUserConfigurationAndSwitchesOwnedFields(t *testing.T) {
	root := t.TempDir()
	configPath := filepath.Join(root, "instance", "config", "config.json")
	templatePath := filepath.Join(root, "server.json")
	existingRaw := writeJSONFixture(t, configPath, map[string]any{
		"schema_version":  float64(2),
		"instance_id":     "studio-a",
		"install_profile": "DESKTOP",
		"network": map[string]any{
			"mode": "LOCAL_ONLY", "host": "127.0.0.1", "port": float64(4321),
			"allowed_origins":             []any{"http://192.168.10.4:4321"},
			"firewall_remote_address":     "192.168.10.0/24",
			"trusted_lan_unauthenticated": false,
		},
		"storage": map[string]any{"projects_root": "D:/studio/projects"},
		"runtime": map[string]any{"worker_channels": []any{"CPU", "GPU_H3"}, "future_runtime_key": "preserve-me"},
	})
	writeJSONFixture(t, templatePath, map[string]any{
		"schema_version":  float64(2),
		"instance_id":     "default",
		"install_profile": "SERVER",
		"network": map[string]any{
			"mode": "LAN_SERVICE", "host": "0.0.0.0", "port": float64(3210),
			"trusted_lan_unauthenticated": true,
		},
		"storage":       map[string]any{"data_root": "${INSTANCE_ROOT}/data"},
		"frontend_dist": "${RELEASE_ROOT}/web",
	})

	first, err := applyProfileTemplate(configPath, templatePath, filepath.Join(root, "backups"))
	if err != nil {
		t.Fatal(err)
	}
	if !first.Changed || first.Created || first.BackupPath == "" {
		t.Fatalf("unexpected first result: %+v", first)
	}
	backup, err := os.ReadFile(first.BackupPath)
	if err != nil || !reflect.DeepEqual(backup, existingRaw) {
		t.Fatalf("profile backup did not preserve the previous config: %v", err)
	}
	configured, err := readJSONObject(configPath)
	if err != nil {
		t.Fatal(err)
	}
	if configured["install_profile"] != "SERVER" {
		t.Fatalf("profile was not switched: %+v", configured)
	}
	network := configured["network"].(map[string]any)
	if network["mode"] != "LAN_SERVICE" || network["host"] != "0.0.0.0" || network["trusted_lan_unauthenticated"] != true {
		t.Fatalf("profile-owned network values were not switched: %+v", network)
	}
	if network["port"] != float64(4321) {
		t.Fatalf("custom port was not preserved: %+v", network)
	}
	if network["firewall_remote_address"] != "192.168.10.0/24" {
		t.Fatalf("custom firewall scope was not preserved: %+v", network)
	}
	if network["allowed_origins"].([]any)[0] != "http://192.168.10.4:4321" {
		t.Fatalf("custom origins were not preserved: %+v", network)
	}
	if configured["storage"].(map[string]any)["data_root"] != "${INSTANCE_ROOT}/data" {
		t.Fatalf("new template defaults were not added: %+v", configured["storage"])
	}
	runtime := configured["runtime"].(map[string]any)
	if runtime["future_runtime_key"] != "preserve-me" {
		t.Fatalf("unknown user configuration was not preserved: %+v", runtime)
	}

	second, err := applyProfileTemplate(configPath, templatePath, filepath.Join(root, "backups"))
	if err != nil {
		t.Fatal(err)
	}
	if second.Changed || second.BackupPath != "" {
		t.Fatalf("idempotent profile application unexpectedly mutated: %+v", second)
	}
}

func TestProfileTemplateRejectsUnsafeServerDefaults(t *testing.T) {
	template := map[string]any{
		"schema_version":  float64(2),
		"install_profile": "SERVER",
		"network": map[string]any{
			"mode": "LAN_SERVICE", "host": "0.0.0.0", "trusted_lan_unauthenticated": false,
		},
	}
	_, _, err := profileOwnedValues(template)
	if err == nil || !strings.Contains(err.Error(), "explicitly enable") {
		t.Fatalf("unsafe server template unexpectedly passed: %v", err)
	}
}

func TestApplyProfileTemplateRejectsSchemaDowngrade(t *testing.T) {
	root := t.TempDir()
	configPath := filepath.Join(root, "config.json")
	templatePath := filepath.Join(root, "server.json")
	writeJSONFixture(t, configPath, map[string]any{"schema_version": float64(3)})
	writeJSONFixture(t, templatePath, map[string]any{
		"schema_version":  float64(2),
		"install_profile": "SERVER",
		"network": map[string]any{
			"mode": "LAN_SERVICE", "host": "0.0.0.0", "trusted_lan_unauthenticated": true,
		},
	})
	if _, err := applyProfileTemplate(configPath, templatePath, filepath.Join(root, "backups")); err == nil || !strings.Contains(err.Error(), "matching config schemas") {
		t.Fatalf("schema downgrade unexpectedly passed: %v", err)
	}
}

func TestFirewallArgumentsAreScopedToConfiguredPortAndRemoteNetwork(t *testing.T) {
	arguments, err := firewallAddArguments(4321, "192.168.10.0/24")
	if err != nil {
		t.Fatal(err)
	}
	joined := strings.Join(arguments, " ")
	for _, expected := range []string{"localport=4321", "remoteip=192.168.10.0/24", "profile=any", "edge=no"} {
		if !strings.Contains(joined, expected) {
			t.Fatalf("firewall arguments missing %q: %s", expected, joined)
		}
	}
	if _, err := firewallAddArguments(0, "LocalSubnet"); err == nil {
		t.Fatal("invalid firewall port unexpectedly passed")
	}
	if _, err := validateFirewallRemoteAddress("any"); err == nil {
		t.Fatal("unscoped firewall remote address unexpectedly passed")
	}
}

func TestPersistFirewallRemoteAddressBacksUpAndIsIdempotent(t *testing.T) {
	root := t.TempDir()
	configPath := filepath.Join(root, "config", "config.json")
	original := writeJSONFixture(t, configPath, map[string]any{
		"schema_version": float64(1),
		"network": map[string]any{
			"mode": "LAN_SERVICE", "host": "0.0.0.0", "port": float64(3210),
			"firewall_remote_address": "LocalSubnet", "trusted_lan_unauthenticated": true,
		},
	})
	backups := filepath.Join(root, "backups")
	backup, err := persistFirewallRemoteAddress(configPath, backups, "192.168.10.0/24")
	if err != nil {
		t.Fatal(err)
	}
	backupRaw, err := os.ReadFile(backup)
	if err != nil || !reflect.DeepEqual(backupRaw, original) {
		t.Fatalf("firewall scope backup mismatch: %v", err)
	}
	configured, err := readJSONObject(configPath)
	if err != nil {
		t.Fatal(err)
	}
	network := configured["network"].(map[string]any)
	if network["firewall_remote_address"] != "192.168.10.0/24" {
		t.Fatalf("firewall scope was not persisted: %+v", network)
	}
	secondBackup, err := persistFirewallRemoteAddress(configPath, backups, "192.168.10.0/24")
	if err != nil || secondBackup != "" {
		t.Fatalf("idempotent firewall persistence mutated config: backup=%q err=%v", secondBackup, err)
	}
}

func TestMachineNetworkRequiresExplicitTrustedLANAcceptance(t *testing.T) {
	var config machineConfig
	config.Network.Mode = "LAN_SERVICE"
	if err := validateMachineNetwork(config); err == nil {
		t.Fatal("LAN service without explicit trust unexpectedly passed")
	}
	config.Network.TrustedLANUnauthenticated = true
	if err := validateMachineNetwork(config); err != nil {
		t.Fatal(err)
	}
}

func TestApplyModelRootPreservesCustomLibrariesAndNeverMovesExistingModels(t *testing.T) {
	root := t.TempDir()
	configPath := filepath.Join(root, "config", "config.json")
	oldLibrary := filepath.Join(root, "legacy-library")
	oldModel := filepath.Join(oldLibrary, "existing.safetensors")
	if err := os.MkdirAll(oldLibrary, 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(oldModel, []byte("model"), 0o600); err != nil {
		t.Fatal(err)
	}
	writeJSONFixture(t, configPath, map[string]any{
		"schema_version": float64(2),
		"runtime": map[string]any{
			"model_root":          filepath.Join(root, "old-model-root"),
			"model_library_roots": []any{oldLibrary},
		},
	})
	newRoot := filepath.Join(root, "new-model-root")
	if err := ensureModelRootDirectories(newRoot); err != nil {
		t.Fatal(err)
	}
	result, err := applyModelRoot(configPath, filepath.Join(root, "backups"), newRoot)
	if err != nil {
		t.Fatal(err)
	}
	if !result.Changed || result.BackupPath == "" {
		t.Fatalf("expected backed-up ModelRoot change, got %+v", result)
	}
	configured, err := readJSONObject(configPath)
	if err != nil {
		t.Fatal(err)
	}
	runtimeConfig := configured["runtime"].(map[string]any)
	if runtimeConfig["model_root"] != newRoot {
		t.Fatalf("ModelRoot not updated: %+v", runtimeConfig)
	}
	if roots := runtimeConfig["model_library_roots"].([]any); len(roots) != 1 || roots[0] != oldLibrary {
		t.Fatalf("custom model libraries were changed: %+v", roots)
	}
	if _, err := os.Stat(oldModel); err != nil {
		t.Fatalf("existing model was moved or deleted: %v", err)
	}
	for _, relative := range canonicalModelDirectories {
		if info, err := os.Stat(filepath.Join(newRoot, filepath.FromSlash(relative))); err != nil || !info.IsDir() {
			t.Fatalf("canonical ModelRoot directory missing %s: %v", relative, err)
		}
	}
}

func TestRequireRuntimeStoppedRejectsLiveHost(t *testing.T) {
	root := t.TempDir()
	paths := hostPaths{RuntimeRoot: filepath.Join(root, "runtime")}
	if err := os.MkdirAll(paths.RuntimeRoot, 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(paths.RuntimeRoot, "host-state.json"), []byte(`{"status":"RUNNING"}`), 0o600); err != nil {
		t.Fatal(err)
	}
	if err := requireRuntimeStopped(paths); err == nil || !strings.Contains(err.Error(), "stop Local Drama Studio") {
		t.Fatalf("running host unexpectedly allowed ModelRoot change: %v", err)
	}
}
