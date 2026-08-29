package main

import (
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"net"
	"os"
	"path/filepath"
	"reflect"
	"strings"
	"time"
)

type profileConfigurationResult struct {
	ConfigPath string `json:"config_path"`
	Profile    string `json:"profile"`
	BackupPath string `json:"backup_path,omitempty"`
	Created    bool   `json:"created"`
	Changed    bool   `json:"changed"`
}

func configureProfileCLI(args []string) error {
	flags := flag.NewFlagSet("configure-profile", flag.ContinueOnError)
	templatePath := flags.String("template", "", "profile configuration template")
	configOverride := flags.String("config", "", "machine config path")
	if err := flags.Parse(args); err != nil {
		return err
	}
	if strings.TrimSpace(*templatePath) == "" {
		return errors.New("configure-profile requires --template")
	}
	paths, _, err := discover(*configOverride)
	if err != nil {
		return err
	}
	result, err := applyProfileTemplate(
		paths.ConfigPath,
		*templatePath,
		filepath.Join(paths.InstanceRoot, "backups", "config"),
	)
	if err != nil {
		return err
	}
	encoded, _ := json.Marshal(result)
	fmt.Println(string(encoded))
	return nil
}

func applyProfileTemplate(configPath string, templatePath string, backupsRoot string) (profileConfigurationResult, error) {
	template, err := readJSONObject(templatePath)
	if err != nil {
		return profileConfigurationResult{}, fmt.Errorf("invalid profile template %s: %w", templatePath, err)
	}
	profile, network, err := profileOwnedValues(template)
	if err != nil {
		return profileConfigurationResult{}, err
	}
	result := cloneJSONObject(template)
	created := true
	backupPath := ""
	var existing map[string]any
	var existingRaw []byte
	if raw, readErr := os.ReadFile(configPath); readErr == nil {
		created = false
		existingRaw = raw
		if err := json.Unmarshal(raw, &existing); err != nil {
			return profileConfigurationResult{}, fmt.Errorf("existing machine config is invalid: %w", err)
		}
		existingVersion, existingOK := existing["schema_version"].(float64)
		templateVersion, templateOK := template["schema_version"].(float64)
		if !existingOK || !templateOK || existingVersion != templateVersion {
			return profileConfigurationResult{}, fmt.Errorf(
				"profile configuration requires matching config schemas (existing=%v template=%v); run config migration first",
				existing["schema_version"], template["schema_version"],
			)
		}
		deepMerge(result, existing)
	} else if !errors.Is(readErr, os.ErrNotExist) {
		return profileConfigurationResult{}, readErr
	}

	// Profile-owned values must switch together. User-owned runtime, storage,
	// tool, port and origin settings survive upgrades and profile changes.
	result["schema_version"] = template["schema_version"]
	result["install_profile"] = profile
	mergedNetwork, _ := result["network"].(map[string]any)
	if mergedNetwork == nil {
		mergedNetwork = map[string]any{}
		result["network"] = mergedNetwork
	}
	for _, key := range []string{"mode", "host", "trusted_lan_unauthenticated"} {
		mergedNetwork[key] = network[key]
	}
	if !created && reflect.DeepEqual(existing, result) {
		return profileConfigurationResult{ConfigPath: configPath, Profile: profile, Created: false, Changed: false}, nil
	}
	if !created {
		backupPath, err = writeConfigBackup(backupsRoot, "config-before-profile-"+strings.ToLower(profile), existingRaw)
		if err != nil {
			return profileConfigurationResult{}, err
		}
	}

	if err := writeJSONAtomic(configPath, result); err != nil {
		return profileConfigurationResult{}, err
	}
	return profileConfigurationResult{ConfigPath: configPath, Profile: profile, BackupPath: backupPath, Created: created, Changed: true}, nil
}

func writeConfigBackup(backupsRoot string, prefix string, raw []byte) (string, error) {
	if err := os.MkdirAll(backupsRoot, 0o750); err != nil {
		return "", err
	}
	path := filepath.Join(backupsRoot, prefix+"-"+time.Now().UTC().Format("20060102T150405.000000000Z07")+".json")
	if err := os.WriteFile(path, raw, 0o600); err != nil {
		return "", err
	}
	return path, nil
}

func profileOwnedValues(template map[string]any) (string, map[string]any, error) {
	profile, _ := template["install_profile"].(string)
	profile = strings.ToUpper(strings.TrimSpace(profile))
	if profile != "DESKTOP" && profile != "SERVER" {
		return "", nil, errors.New("profile template install_profile must be DESKTOP or SERVER")
	}
	if version, ok := template["schema_version"].(float64); !ok || version != configSchemaSupported {
		return "", nil, fmt.Errorf("profile template schema_version must be %d", configSchemaSupported)
	}
	network, ok := template["network"].(map[string]any)
	if !ok {
		return "", nil, errors.New("profile template must contain a network object")
	}
	mode, modeOK := network["mode"].(string)
	host, hostOK := network["host"].(string)
	trusted, trustOK := network["trusted_lan_unauthenticated"].(bool)
	if !modeOK || !hostOK || !trustOK {
		return "", nil, errors.New("profile template network must define mode, host and trusted_lan_unauthenticated")
	}
	serverIP := net.ParseIP(strings.TrimSpace(host))
	if profile == "SERVER" && (mode != "LAN_SERVICE" || serverIP == nil || serverIP.IsLoopback() || !trusted) {
		return "", nil, errors.New("SERVER profile must explicitly enable trusted LAN service mode")
	}
	desktopHost := strings.ToLower(strings.TrimSpace(host))
	if profile == "DESKTOP" && (mode != "LOCAL_ONLY" || (desktopHost != "127.0.0.1" && desktopHost != "localhost" && desktopHost != "::1") || trusted) {
		return "", nil, errors.New("DESKTOP profile must use authenticated local-only mode")
	}
	return profile, network, nil
}

func readJSONObject(path string) (map[string]any, error) {
	raw, err := os.ReadFile(path)
	if err != nil {
		return nil, err
	}
	var value map[string]any
	if err := json.Unmarshal(raw, &value); err != nil {
		return nil, err
	}
	return value, nil
}

func cloneJSONObject(value map[string]any) map[string]any {
	raw, _ := json.Marshal(value)
	var cloned map[string]any
	_ = json.Unmarshal(raw, &cloned)
	return cloned
}

func deepMerge(target map[string]any, source map[string]any) {
	for key, value := range source {
		if sourceMap, ok := value.(map[string]any); ok {
			if targetMap, exists := target[key].(map[string]any); exists {
				deepMerge(targetMap, sourceMap)
				continue
			}
		}
		target[key] = value
	}
}

func writeJSONAtomic(path string, value map[string]any) error {
	if err := os.MkdirAll(filepath.Dir(path), 0o750); err != nil {
		return err
	}
	temporary := path + ".profile.partial"
	file, err := os.OpenFile(temporary, os.O_CREATE|os.O_TRUNC|os.O_WRONLY, 0o600)
	if err != nil {
		return err
	}
	encoder := json.NewEncoder(file)
	encoder.SetIndent("", "  ")
	encodeErr := encoder.Encode(value)
	if encodeErr == nil {
		encodeErr = file.Sync()
	}
	closeErr := file.Close()
	if encodeErr != nil {
		_ = os.Remove(temporary)
		return encodeErr
	}
	if closeErr != nil {
		_ = os.Remove(temporary)
		return closeErr
	}
	if err := atomicReplace(temporary, path); err != nil {
		_ = os.Remove(temporary)
		return err
	}
	return nil
}

func validateMachineNetwork(config machineConfig) error {
	mode := strings.ToUpper(strings.TrimSpace(config.Network.Mode))
	if mode == "" {
		mode = "LOCAL_ONLY"
	}
	switch mode {
	case "LOCAL_ONLY":
		if config.Network.TrustedLANUnauthenticated {
			return errors.New("LOCAL_ONLY must not enable trusted_lan_unauthenticated")
		}
	case "LAN_SERVICE":
		if !config.Network.TrustedLANUnauthenticated {
			return errors.New("LAN_SERVICE requires explicit trusted_lan_unauthenticated=true until administrator authentication is configured")
		}
	default:
		return fmt.Errorf("unsupported network mode %q", config.Network.Mode)
	}
	return nil
}
