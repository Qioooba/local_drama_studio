package main

import (
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"os"
	"path/filepath"
	"runtime"
	"strings"
)

var canonicalModelDirectories = []string{
	"downloads",
	"staging",
	"quarantine",
	"libraries/comfyui",
	"libraries/pytorch",
	"libraries/ollama",
	"libraries/audio",
}

var canonicalModelLibraryRoots = []any{
	"${MODEL_ROOT}/libraries/comfyui",
	"${MODEL_ROOT}/libraries/pytorch",
	"${MODEL_ROOT}/libraries/ollama",
	"${MODEL_ROOT}/libraries/audio",
}

type modelRootConfigurationResult struct {
	ConfigPath string `json:"config_path"`
	ModelRoot  string `json:"model_root"`
	BackupPath string `json:"backup_path,omitempty"`
	Changed    bool   `json:"changed"`
}

// configureModelRootCLI changes only the machine-owned storage root. It never
// moves, deletes, or re-registers model files; custom library bindings remain
// intact and administrators rescan explicitly after a deliberate data move.
func configureModelRootCLI(args []string) error {
	flags := flag.NewFlagSet("configure-model-root", flag.ContinueOnError)
	root := flags.String("path", "", "absolute local ModelRoot path")
	configOverride := flags.String("config", "", "machine config path")
	if err := flags.Parse(args); err != nil {
		return err
	}
	if strings.TrimSpace(*root) == "" {
		return errors.New("configure-model-root requires --path")
	}
	if !filepath.IsAbs(*root) {
		return errors.New("configure-model-root --path must be absolute")
	}
	if runtime.GOOS == "windows" && strings.HasPrefix(filepath.Clean(*root), `\\`) {
		return errors.New("configure-model-root requires a local volume, not a UNC share")
	}
	paths, _, err := discover(*configOverride)
	if err != nil {
		return err
	}
	if err := requireRuntimeStopped(paths); err != nil {
		return err
	}
	configuredRoot, err := filepath.Abs(filepath.Clean(*root))
	if err != nil {
		return fmt.Errorf("resolve ModelRoot: %w", err)
	}
	if err := ensureModelRootDirectories(configuredRoot); err != nil {
		return err
	}
	result, err := applyModelRoot(paths.ConfigPath, filepath.Join(paths.InstanceRoot, "backups", "config"), configuredRoot)
	if err != nil {
		return err
	}
	raw, _ := json.Marshal(result)
	fmt.Println(string(raw))
	return nil
}

func requireRuntimeStopped(paths hostPaths) error {
	raw, err := os.ReadFile(filepath.Join(paths.RuntimeRoot, "host-state.json"))
	if errors.Is(err, os.ErrNotExist) {
		return nil
	}
	if err != nil {
		return err
	}
	var state processState
	if err := json.Unmarshal(raw, &state); err != nil {
		return fmt.Errorf("invalid runtime state: %w", err)
	}
	switch state.Status {
	case "STARTING", "MAINTENANCE", "WAIT_API_READY", "RUNNING", "DRAINING":
		return errors.New("stop Local Drama Studio before changing ModelRoot")
	default:
		return nil
	}
}

func ensureModelRootDirectories(root string) error {
	for _, relative := range append([]string{"."}, canonicalModelDirectories...) {
		path := filepath.Join(root, filepath.FromSlash(relative))
		if err := os.MkdirAll(path, 0o750); err != nil {
			return fmt.Errorf("create ModelRoot directory %s: %w", path, err)
		}
		info, err := os.Stat(path)
		if err != nil || !info.IsDir() {
			return fmt.Errorf("ModelRoot path is not a directory: %s", path)
		}
	}
	return nil
}

func applyModelRoot(configPath string, backupsRoot string, modelRoot string) (modelRootConfigurationResult, error) {
	config, err := readJSONObject(configPath)
	if err != nil {
		if errors.Is(err, os.ErrNotExist) {
			return modelRootConfigurationResult{}, errors.New("machine config does not exist; install a profile before configuring ModelRoot")
		}
		return modelRootConfigurationResult{}, err
	}
	version, ok := config["schema_version"].(float64)
	if !ok || version != configSchemaSupported {
		return modelRootConfigurationResult{}, fmt.Errorf("configure-model-root requires config schema %d; run upgrade first", configSchemaSupported)
	}
	runtimeConfig, ok := config["runtime"].(map[string]any)
	if !ok {
		runtimeConfig = map[string]any{}
		config["runtime"] = runtimeConfig
	}
	changed := runtimeConfig["model_root"] != modelRoot
	runtimeConfig["model_root"] = modelRoot
	if roots, exists := runtimeConfig["model_library_roots"]; !exists || !validStringList(roots) || len(roots.([]any)) == 0 {
		runtimeConfig["model_library_roots"] = canonicalModelLibraryRoots
		changed = true
	}
	if !changed {
		return modelRootConfigurationResult{ConfigPath: configPath, ModelRoot: modelRoot, Changed: false}, nil
	}
	original, err := os.ReadFile(configPath)
	if err != nil {
		return modelRootConfigurationResult{}, err
	}
	backup, err := writeConfigBackup(backupsRoot, "config-before-model-root", original)
	if err != nil {
		return modelRootConfigurationResult{}, err
	}
	if err := writeJSONAtomic(configPath, config); err != nil {
		return modelRootConfigurationResult{}, err
	}
	return modelRootConfigurationResult{ConfigPath: configPath, ModelRoot: modelRoot, BackupPath: backup, Changed: true}, nil
}

func validStringList(value any) bool {
	items, ok := value.([]any)
	if !ok {
		return false
	}
	for _, item := range items {
		if _, ok := item.(string); !ok {
			return false
		}
	}
	return true
}
