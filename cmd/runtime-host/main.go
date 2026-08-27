package main

import (
	"context"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"os"
	"os/exec"
	"os/signal"
	"path/filepath"
	"runtime"
	"strconv"
	"syscall"
	"time"
)

var hostVersion = "development"
var trustedReleasePublicKey = ""

type activeRelease struct {
	SchemaVersion int    `json:"schema_version"`
	Active        string `json:"active"`
	Previous      string `json:"previous"`
	Generation    int    `json:"generation"`
}

type machineConfig struct {
	SchemaVersion  int    `json:"schema_version"`
	InstanceID     string `json:"instance_id"`
	InstallProfile string `json:"install_profile"`
	Network        struct {
		Host string `json:"host"`
		Port int    `json:"port"`
	} `json:"network"`
}

type processState struct {
	SchemaVersion  int       `json:"schema_version"`
	HostVersion    string    `json:"host_version"`
	HostPID        int       `json:"host_pid"`
	APIPID         int       `json:"api_pid,omitempty"`
	WorkerPID      int       `json:"worker_pid,omitempty"`
	ReleaseVersion string    `json:"release_version"`
	Status         string    `json:"status"`
	StartedAt      time.Time `json:"started_at"`
	UpdatedAt      time.Time `json:"updated_at"`
}

type lockState struct {
	PID int `json:"pid"`
}

// hostPaths holds machine-scope paths (filled by discover) plus release-scope
// facts (filled by resolveRelease). Fields are intentionally two-phase:
// stop/status must keep working even when the release payload is corrupt.
type hostPaths struct {
	InstallRoot  string
	InstanceRoot string
	ConfigPath   string
	RuntimeRoot  string
	ReleaseRoot  string
	Python       string
	AppRoot      string
	Version      string
	HostProtocol int
}

func main() {
	if err := runCLI(os.Args[1:]); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
}

func runCLI(args []string) error {
	if len(args) == 0 {
		args = []string{"run"}
	}
	switch args[0] {
	case "run":
		return runForeground(args[1:])
	case "service":
		return runService()
	case "install-service":
		return installService()
	case "uninstall-service":
		return uninstallService()
	case "status":
		return printStatus()
	case "stop":
		return requestStop()
	case "doctor":
		return doctor()
	case "verify-release":
		return verifyReleaseCLI(args[1:])
	case "upgrade":
		return upgradeCLI(args[1:])
	case "rollback":
		return rollbackCLI(args[1:])
	case "version":
		fmt.Println(hostVersion)
		return nil
	default:
		return fmt.Errorf("unknown command %q (run, service, install-service, uninstall-service, status, stop, doctor, verify-release, upgrade, rollback, version)", args[0])
	}
}

func runForeground(args []string) error {
	flags := flag.NewFlagSet("run", flag.ContinueOnError)
	config := flags.String("config", "", "machine config path")
	if err := flags.Parse(args); err != nil {
		return err
	}
	ctx, cancel := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer cancel()
	for attempt := 0; ; attempt++ {
		err := supervise(ctx, *config)
		if err == nil || ctx.Err() != nil || attempt >= 2 {
			return err
		}
		delay := time.Duration(1<<attempt) * time.Second
		fmt.Fprintf(os.Stderr, "Runtime Host child failure; restart attempt %d in %s: %v\n", attempt+1, delay, err)
		select {
		case <-ctx.Done():
			return nil
		case <-time.After(delay):
		}
	}
}

func discover(configOverride string) (hostPaths, machineConfig, error) {
	executable, err := os.Executable()
	if err != nil {
		return hostPaths{}, machineConfig{}, err
	}
	executable, _ = filepath.Abs(executable)
	installRoot := os.Getenv("LOCAL_DRAMA_INSTALL_ROOT")
	developmentRoot := false
	if installRoot == "" {
		executableDir := filepath.Dir(executable)
		for candidate, depth := executableDir, 0; depth < 6; depth++ {
			if _, err := os.Stat(filepath.Join(candidate, "apps", "api", "local_drama")); err == nil {
				installRoot = candidate
				developmentRoot = true
				break
			}
			parent := filepath.Dir(candidate)
			if parent == candidate {
				break
			}
			candidate = parent
		}
		if installRoot == "" {
			if filepath.Base(executableDir) == "host" {
				installRoot = filepath.Dir(executableDir)
			} else {
				installRoot = executableDir
			}
		}
	}
	instanceRoot := os.Getenv("LOCAL_DRAMA_INSTANCE_ROOT")
	if instanceRoot == "" {
		if developmentRoot {
			instanceRoot = installRoot
		} else if runtime.GOOS == "windows" {
			base := os.Getenv("PROGRAMDATA")
			if base == "" {
				base = os.Getenv("LOCALAPPDATA")
			}
			instanceRoot = filepath.Join(base, "LocalDramaStudio")
		} else {
			instanceRoot = "/var/lib/local-drama-studio"
		}
	}
	configPath := configOverride
	if configPath == "" {
		configPath = os.Getenv("LOCAL_DRAMA_CONFIG")
	}
	if configPath == "" {
		configPath = filepath.Join(instanceRoot, "config", "config.json")
	}
	config := machineConfig{SchemaVersion: 1, InstanceID: "default", InstallProfile: "DESKTOP"}
	config.Network.Host = "127.0.0.1"
	config.Network.Port = 3210
	if raw, err := os.ReadFile(configPath); err == nil {
		if err := json.Unmarshal(raw, &config); err != nil {
			return hostPaths{}, machineConfig{}, fmt.Errorf("invalid config %s: %w", configPath, err)
		}
	} else if !errors.Is(err, os.ErrNotExist) {
		return hostPaths{}, machineConfig{}, err
	}
	if configuredHost := os.Getenv("LOCAL_DRAMA_HOST"); configuredHost != "" {
		config.Network.Host = configuredHost
	}
	if configuredPort := os.Getenv("LOCAL_DRAMA_PORT"); configuredPort != "" {
		port, parseErr := strconv.Atoi(configuredPort)
		if parseErr != nil || port < 1 || port > 65535 {
			return hostPaths{}, machineConfig{}, fmt.Errorf("invalid LOCAL_DRAMA_PORT %q", configuredPort)
		}
		config.Network.Port = port
	}
	return hostPaths{
		InstallRoot: installRoot, InstanceRoot: instanceRoot, ConfigPath: configPath,
		RuntimeRoot: filepath.Join(instanceRoot, "runtime"),
	}, config, nil
}

// resolveRelease fills the release-scope fields of paths: the active release
// pointer, the canonical release version identity, the private Python and the
// app root. It is kept separate from discover so machine-scope commands
// (status/stop) keep working when the release payload is missing or corrupt.
func resolveRelease(paths *hostPaths) error {
	releaseRoot := paths.InstallRoot
	var pointerVersion string
	activePath := filepath.Join(paths.InstallRoot, "active-release.json")
	if raw, err := os.ReadFile(activePath); err == nil {
		var active activeRelease
		if err := json.Unmarshal(raw, &active); err != nil || !safeVersion.MatchString(active.Active) {
			return fmt.Errorf("invalid active release: %s", activePath)
		}
		pointerVersion = active.Active
		releaseRoot = filepath.Join(paths.InstallRoot, "versions", active.Active)
		if _, statErr := os.Stat(releaseRoot); errors.Is(statErr, os.ErrNotExist) {
			portablePayload := filepath.Join(paths.InstallRoot, "payload")
			if _, payloadErr := os.Stat(filepath.Join(portablePayload, "release-manifest.json")); payloadErr == nil {
				releaseRoot = portablePayload
			}
		}
	} else if !errors.Is(err, os.ErrNotExist) {
		return err
	}
	identity, err := loadReleaseIdentity(releaseRoot, paths.InstallRoot)
	if err != nil {
		return err
	}
	if pointerVersion != "" && identity.Version != pointerVersion {
		return fmt.Errorf(
			"active release %s does not match its version identity %s (release version identity is the single source of truth; reinstall the release)",
			pointerVersion, identity.Version,
		)
	}
	python := os.Getenv("LOCAL_DRAMA_PYTHON")
	if python == "" {
		candidates := []string{
			filepath.Join(releaseRoot, "runtime", "python", "python.exe"),
			filepath.Join(releaseRoot, "runtime", "python", "bin", "python3"),
			filepath.Join(paths.InstallRoot, ".venv", "Scripts", "python.exe"),
		}
		for _, candidate := range candidates {
			if info, err := os.Stat(candidate); err == nil && !info.IsDir() {
				python = candidate
				break
			}
		}
	}
	if python == "" {
		return errors.New("private Python runtime not found")
	}
	appRoot := filepath.Join(releaseRoot, "app")
	if _, err := os.Stat(filepath.Join(paths.InstallRoot, "apps", "api", "local_drama")); err == nil {
		appRoot = filepath.Join(paths.InstallRoot, "apps", "api")
	}
	paths.ReleaseRoot = releaseRoot
	paths.Python = python
	paths.AppRoot = appRoot
	paths.Version = identity.Version
	paths.HostProtocol = identity.HostProtocol
	return nil
}

func supervise(ctx context.Context, configOverride string) error {
	paths, config, err := discover(configOverride)
	if err != nil {
		return err
	}
	if err := resolveRelease(&paths); err != nil {
		return err
	}
	if err := os.MkdirAll(paths.RuntimeRoot, 0o750); err != nil {
		return err
	}
	lockPath := filepath.Join(paths.RuntimeRoot, "host.lock")
	lock, err := acquireHostLock(lockPath)
	if err != nil {
		return fmt.Errorf("another Host instance may be running: %w", err)
	}
	defer func() { _ = lock.Close(); _ = os.Remove(lockPath) }()
	_, _ = fmt.Fprintf(lock, "{\"pid\":%d,\"started_at\":%q}\n", os.Getpid(), time.Now().UTC())

	stopFile := filepath.Join(paths.RuntimeRoot, "host.stop")
	workerStop := filepath.Join(paths.RuntimeRoot, "worker.stop")
	_ = os.Remove(stopFile)
	_ = os.Remove(workerStop)
	state := processState{1, hostVersion, os.Getpid(), 0, 0, paths.Version, "STARTING", time.Now().UTC(), time.Now().UTC()}
	if err := writeState(paths, &state); err != nil {
		return err
	}
	state.Status = "MAINTENANCE"
	if err := writeState(paths, &state); err != nil {
		return err
	}
	if err := runMaintenance(paths, "config-upgrade"); err != nil {
		state.Status = "MAINTENANCE_FAILED"
		_ = writeState(paths, &state)
		return fmt.Errorf("startup config maintenance failed: %w", err)
	}
	if err := runMaintenance(paths, "upgrade"); err != nil {
		state.Status = "MAINTENANCE_FAILED"
		_ = writeState(paths, &state)
		return fmt.Errorf("startup database maintenance failed: %w", err)
	}

	api := pythonCommand(ctx, paths, "local_drama.entrypoints.api", "--config", paths.ConfigPath)
	if err := api.Start(); err != nil {
		return fmt.Errorf("start API: %w", err)
	}
	state.APIPID = api.Process.Pid
	state.Status = "WAIT_API_READY"
	_ = writeState(paths, &state)
	port := config.Network.Port
	if port == 0 {
		port = 3210
	}
	if err := waitReady(ctx, port, api, 30*time.Second); err != nil {
		_ = terminate(api, 5*time.Second)
		return err
	}

	workerID := "local-drama-" + config.InstanceID
	worker := pythonCommand(ctx, paths, "local_drama.entrypoints.worker",
		"--config", paths.ConfigPath,
		"--worker-id", workerID,
		"--worker-version", paths.Version,
		"--api-version", paths.Version,
		"--watch", "--poll-seconds", "1", "--stop-file", workerStop)
	if err := worker.Start(); err != nil {
		_ = terminate(api, 5*time.Second)
		return fmt.Errorf("start Worker: %w", err)
	}
	state.WorkerPID = worker.Process.Pid
	state.Status = "RUNNING"
	_ = writeState(paths, &state)

	apiDone := make(chan error, 1)
	workerDone := make(chan error, 1)
	go func() { apiDone <- api.Wait() }()
	go func() { workerDone <- worker.Wait() }()
	ticker := time.NewTicker(time.Second)
	defer ticker.Stop()
	var failure error
selectLoop:
	for {
		select {
		case <-ctx.Done():
			break selectLoop
		case err := <-apiDone:
			failure = childExitError("API", err)
			break selectLoop
		case err := <-workerDone:
			failure = childExitError("Worker", err)
			break selectLoop
		case <-ticker.C:
			if _, err := os.Stat(stopFile); err == nil {
				break selectLoop
			}
		}
	}
	state.Status = "DRAINING"
	_ = writeState(paths, &state)
	_ = os.WriteFile(workerStop, []byte("stop\n"), 0o600)
	_ = terminate(worker, 10*time.Second)
	_ = terminate(api, 10*time.Second)
	state.Status = "STOPPED"
	state.APIPID, state.WorkerPID = 0, 0
	_ = writeState(paths, &state)
	_ = os.Remove(stopFile)
	_ = os.Remove(workerStop)
	return failure
}

func acquireHostLock(path string) (*os.File, error) {
	for attempt := 0; attempt < 2; attempt++ {
		lock, err := os.OpenFile(path, os.O_CREATE|os.O_EXCL|os.O_WRONLY, 0o600)
		if err == nil {
			return lock, nil
		}
		if !errors.Is(err, os.ErrExist) {
			return nil, err
		}
		raw, readErr := os.ReadFile(path)
		var state lockState
		if readErr != nil || json.Unmarshal(raw, &state) != nil || state.PID <= 0 || processExists(state.PID) {
			return nil, err
		}
		if removeErr := os.Remove(path); removeErr != nil {
			return nil, removeErr
		}
	}
	return nil, errors.New("could not acquire Host lock")
}

func childExitError(role string, err error) error {
	if err == nil {
		return fmt.Errorf("%s exited unexpectedly with status 0", role)
	}
	return fmt.Errorf("%s exited: %w", role, err)
}

func pythonCommand(_ context.Context, paths hostPaths, module string, args ...string) *exec.Cmd {
	commandArgs := append([]string{"-m", module}, args...)
	cmd := exec.Command(paths.Python, commandArgs...)
	cmd.Dir = paths.ReleaseRoot
	cmd.Stdout = os.Stdout
	cmd.Stderr = os.Stderr
	configureChildProcess(cmd)
	env := os.Environ()
	env = append(env,
		"LOCAL_DRAMA_RELEASE_ROOT="+paths.ReleaseRoot,
		"LOCAL_DRAMA_INSTANCE_ROOT="+paths.InstanceRoot,
		"LOCAL_DRAMA_CONFIG="+paths.ConfigPath,
		"PYTHONPATH="+paths.AppRoot,
		"LOCAL_DRAMA_HOST_PROTOCOL="+strconv.Itoa(paths.HostProtocol),
		"LOCAL_DRAMA_RELEASE_VERSION="+paths.Version,
		"LOCAL_DRAMA_PACKAGED=1",
	)
	cmd.Env = env
	return cmd
}

func waitReady(ctx context.Context, port int, command *exec.Cmd, timeout time.Duration) error {
	deadline := time.Now().Add(timeout)
	url := "http://127.0.0.1:" + strconv.Itoa(port) + "/api/v1/health/ready"
	for time.Now().Before(deadline) {
		select {
		case <-ctx.Done():
			return ctx.Err()
		default:
		}
		probe := exec.CommandContext(ctx, command.Path, "-c", "import urllib.request,sys; r=urllib.request.urlopen(sys.argv[1],timeout=2); sys.exit(0 if r.status==200 and b'HEALTHY' in r.read() else 1)", url)
		probe.Env = command.Env
		if probe.Run() == nil {
			return nil
		}
		time.Sleep(250 * time.Millisecond)
	}
	return fmt.Errorf("API readiness timeout at %s", url)
}

func terminate(command *exec.Cmd, timeout time.Duration) error {
	if command == nil || command.Process == nil {
		return nil
	}
	_ = signalChildProcess(command)
	deadline := time.Now().Add(timeout)
	for time.Now().Before(deadline) {
		if command.ProcessState != nil && command.ProcessState.Exited() {
			return nil
		}
		time.Sleep(100 * time.Millisecond)
	}
	return command.Process.Kill()
}

func writeState(paths hostPaths, state *processState) error {
	state.UpdatedAt = time.Now().UTC()
	raw, err := json.MarshalIndent(state, "", "  ")
	if err != nil {
		return err
	}
	target := filepath.Join(paths.RuntimeRoot, "host-state.json")
	temporary := target + ".partial"
	if err := os.WriteFile(temporary, append(raw, '\n'), 0o600); err != nil {
		return err
	}
	return atomicReplace(temporary, target)
}

func printStatus() error {
	paths, _, err := discover("")
	if err != nil {
		return err
	}
	raw, err := os.ReadFile(filepath.Join(paths.RuntimeRoot, "host-state.json"))
	if errors.Is(err, os.ErrNotExist) {
		fmt.Println(`{"status":"NOT_INSTALLED_OR_NEVER_STARTED"}`)
		return nil
	}
	if err != nil {
		return err
	}
	fmt.Print(string(raw))
	return nil
}

func requestStop() error {
	paths, _, err := discover("")
	if err != nil {
		return err
	}
	if err := os.MkdirAll(paths.RuntimeRoot, 0o750); err != nil {
		return err
	}
	return os.WriteFile(filepath.Join(paths.RuntimeRoot, "host.stop"), []byte("stop\n"), 0o600)
}

func doctor() error {
	paths, config, err := discover("")
	result := map[string]any{"schema_version": 1, "host_version": hostVersion, "goos": runtime.GOOS, "goarch": runtime.GOARCH}
	if err != nil {
		result["status"], result["error"] = "FAIL", err.Error()
	} else if releaseErr := resolveRelease(&paths); releaseErr != nil {
		err = releaseErr
		result["status"], result["error"] = "FAIL", releaseErr.Error()
	} else {
		result["status"] = "PASS"
		result["paths"] = paths
		result["config"] = map[string]any{"instance_id": config.InstanceID, "install_profile": config.InstallProfile, "port": config.Network.Port}
	}
	raw, _ := json.MarshalIndent(result, "", "  ")
	fmt.Println(string(raw))
	return err
}
