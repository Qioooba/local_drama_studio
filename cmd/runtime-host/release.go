package main

import (
	"archive/tar"
	"archive/zip"
	"compress/gzip"
	"context"
	"crypto/ed25519"
	"crypto/sha256"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"io"
	"os"
	"os/exec"
	"path/filepath"
	"regexp"
	"runtime"
	"strconv"
	"strings"
	"time"
)

type manifestFile struct {
	Path   string `json:"path"`
	Size   int64  `json:"size"`
	SHA256 string `json:"sha256"`
}

type releaseManifest struct {
	SchemaVersion int    `json:"schema_version"`
	Product       string `json:"product"`
	Version       string `json:"version"`
	BuildID       string `json:"build_id"`
	Channel       string `json:"channel"`
	Platform      struct {
		OS   string `json:"os"`
		Arch string `json:"arch"`
	} `json:"platform"`
	Host struct {
		Protocol       int    `json:"protocol"`
		MinimumVersion string `json:"minimum_version"`
	} `json:"host"`
	ConfigSchema struct {
		Minimum int `json:"minimum"`
		Target  int `json:"target"`
	} `json:"config_schema"`
	Files     []manifestFile `json:"files"`
	Signature *struct {
		Algorithm string `json:"algorithm"`
		KeyID     string `json:"key_id"`
		Value     string `json:"value"`
	} `json:"signature"`
}

type updateSession struct {
	SchemaVersion int       `json:"schema_version"`
	SessionID     string    `json:"session_id"`
	Version       string    `json:"version"`
	Bundle        string    `json:"bundle"`
	Status        string    `json:"status"`
	StartedAt     time.Time `json:"started_at"`
	UpdatedAt     time.Time `json:"updated_at"`
	RecoverySet   string    `json:"recovery_set,omitempty"`
	ConfigBackup  string    `json:"config_backup,omitempty"`
	Error         string    `json:"error,omitempty"`
}

var safeVersion = regexp.MustCompile(`^[0-9A-Za-z][0-9A-Za-z._-]{0,63}$`)
var semanticVersion = regexp.MustCompile(`^[0-9]+\.[0-9]+\.[0-9]+(?:-[0-9A-Za-z.-]+)?$`)

func verifyReleaseCLI(args []string) error {
	flags := flag.NewFlagSet("verify-release", flag.ContinueOnError)
	root := flags.String("path", "", "release payload directory")
	if err := flags.Parse(args); err != nil {
		return err
	}
	if *root == "" {
		paths, _, err := discover("")
		if err != nil {
			return err
		}
		if err := resolveRelease(&paths); err != nil {
			return err
		}
		*root = paths.ReleaseRoot
	}
	manifest, err := verifyRelease(*root)
	result := map[string]any{"schema_version": 1, "operation": "release.verify", "path": *root}
	if err != nil {
		result["status"], result["error"] = "FAIL", err.Error()
	} else {
		result["status"], result["version"], result["files"] = "PASS", manifest.Version, len(manifest.Files)
	}
	raw, _ := json.MarshalIndent(result, "", "  ")
	fmt.Println(string(raw))
	return err
}

func verifyRelease(root string) (releaseManifest, error) {
	manifestPath := filepath.Join(root, "release-manifest.json")
	raw, err := os.ReadFile(manifestPath)
	if err != nil {
		return releaseManifest{}, err
	}
	var manifest releaseManifest
	if err := json.Unmarshal(raw, &manifest); err != nil {
		return releaseManifest{}, fmt.Errorf("invalid manifest: %w", err)
	}
	if manifest.SchemaVersion != 1 || manifest.Product != "LocalDramaStudio" || !safeVersion.MatchString(manifest.Version) || manifest.BuildID == "" || manifest.Channel == "" {
		return releaseManifest{}, errors.New("unsupported release manifest")
	}
	wantedPlatform := runtime.GOOS + "-" + runtime.GOARCH
	actualPlatform := manifest.Platform.OS + "-" + manifest.Platform.Arch
	if actualPlatform != wantedPlatform {
		return releaseManifest{}, fmt.Errorf("release platform %s does not match %s", actualPlatform, wantedPlatform)
	}
	if manifest.Host.Protocol != hostProtocolSupported || manifest.ConfigSchema.Target != configSchemaSupported {
		return releaseManifest{}, errors.New("release protocol is not compatible with this Host")
	}
	// The payload's version.json is the single source of version truth; the
	// manifest must agree with it, otherwise API and Worker would negotiate
	// different versions at runtime.
	identity, err := loadReleaseIdentity(root, root)
	if err != nil {
		return releaseManifest{}, fmt.Errorf("release payload version identity is invalid: %w", err)
	}
	if identity.Version != manifest.Version {
		return releaseManifest{}, fmt.Errorf("release version identity %s does not match manifest version %s", identity.Version, manifest.Version)
	}
	if !semanticVersion.MatchString(manifest.Host.MinimumVersion) {
		return releaseManifest{}, errors.New("release minimum Host version is invalid")
	}
	if hostVersion != "development" && compareVersions(hostVersion, manifest.Host.MinimumVersion) < 0 {
		return releaseManifest{}, fmt.Errorf(
			"release requires Host %s or newer; installed Host is %s (run the platform installer)",
			manifest.Host.MinimumVersion,
			hostVersion,
		)
	}
	if err := verifyManifestSignature(raw, manifest); err != nil {
		return releaseManifest{}, err
	}
	for _, entry := range manifest.Files {
		relative := filepath.Clean(filepath.FromSlash(entry.Path))
		if relative == "." || filepath.IsAbs(relative) || relative == ".." || strings.HasPrefix(relative, ".."+string(filepath.Separator)) {
			return releaseManifest{}, fmt.Errorf("unsafe manifest path %q", entry.Path)
		}
		path := filepath.Join(root, relative)
		info, err := os.Lstat(path)
		if err != nil {
			return releaseManifest{}, fmt.Errorf("missing release file %s: %w", entry.Path, err)
		}
		if !info.Mode().IsRegular() || info.Size() != entry.Size {
			return releaseManifest{}, fmt.Errorf("release file metadata mismatch: %s", entry.Path)
		}
		digest, err := fileSHA256(path)
		if err != nil {
			return releaseManifest{}, err
		}
		if !strings.EqualFold(digest, entry.SHA256) {
			return releaseManifest{}, fmt.Errorf("release file checksum mismatch: %s", entry.Path)
		}
	}
	return manifest, nil
}

func compareVersions(left string, right string) int {
	parse := func(value string) [3]int {
		var result [3]int
		core := strings.SplitN(value, "-", 2)[0]
		parts := strings.Split(core, ".")
		for index := 0; index < len(result) && index < len(parts); index++ {
			result[index], _ = strconv.Atoi(parts[index])
		}
		return result
	}
	a, b := parse(left), parse(right)
	for index := range a {
		if a[index] < b[index] {
			return -1
		}
		if a[index] > b[index] {
			return 1
		}
	}
	return 0
}

func verifyManifestSignature(raw []byte, manifest releaseManifest) error {
	if manifest.Signature == nil {
		if manifest.Channel == "development" && trustedReleasePublicKey == "" {
			return nil
		}
		return errors.New("release manifest is unsigned")
	}
	if manifest.Signature.Algorithm != "Ed25519" {
		return errors.New("unsupported release signature algorithm")
	}
	publicKey, err := base64.StdEncoding.DecodeString(strings.TrimSpace(trustedReleasePublicKey))
	if err != nil || len(publicKey) != ed25519.PublicKeySize {
		return errors.New("Host has no valid trusted release public key")
	}
	keyDigest := sha256.Sum256(publicKey)
	if manifest.Signature.KeyID != hex.EncodeToString(keyDigest[:8]) {
		return errors.New("release signature key id does not match the trusted key")
	}
	signature, err := base64.StdEncoding.DecodeString(manifest.Signature.Value)
	if err != nil {
		return errors.New("release signature is not valid base64")
	}
	var canonical map[string]any
	if err := json.Unmarshal(raw, &canonical); err != nil {
		return err
	}
	canonical["signature"] = nil
	payload, err := json.Marshal(canonical)
	if err != nil {
		return err
	}
	if !ed25519.Verify(ed25519.PublicKey(publicKey), payload, signature) {
		return errors.New("release manifest signature verification failed")
	}
	return nil
}

func fileSHA256(path string) (string, error) {
	stream, err := os.Open(path)
	if err != nil {
		return "", err
	}
	defer stream.Close()
	digest := sha256.New()
	if _, err := io.Copy(digest, stream); err != nil {
		return "", err
	}
	return hex.EncodeToString(digest.Sum(nil)), nil
}

func upgradeCLI(args []string) (resultErr error) {
	flags := flag.NewFlagSet("upgrade", flag.ContinueOnError)
	bundle := flags.String("bundle", "", "extracted release package or payload directory")
	config := flags.String("config", "", "machine config path")
	if err := flags.Parse(args); err != nil {
		return err
	}
	if *bundle == "" {
		return errors.New("--bundle is required")
	}
	paths, machine, err := discover(*config)
	if err != nil {
		return err
	}
	if err := resolveRelease(&paths); err != nil {
		return err
	}
	if err := ensureHostStopped(paths); err != nil {
		return err
	}
	payload, err := materializeBundle(paths, *bundle)
	if err != nil {
		return err
	}
	manifest, err := verifyRelease(payload)
	if err != nil {
		return err
	}
	session := &updateSession{
		SchemaVersion: 1,
		SessionID:     time.Now().UTC().Format("20060102T150405.000000000Z") + "-" + manifest.Version,
		Version:       manifest.Version,
		Bundle:        payload,
		Status:        "RELEASE_VERIFIED",
		StartedAt:     time.Now().UTC(),
	}
	if err := writeUpdateSession(paths, session); err != nil {
		return err
	}
	var rollbackCandidate hostPaths
	databaseUpgraded := false
	committed := false
	defer func() {
		if resultErr != nil && !committed {
			if databaseUpgraded && session.RecoverySet != "" && rollbackCandidate.Python != "" {
				_ = runMaintenance(rollbackCandidate, "recovery-restore", session.RecoverySet)
			}
			if session.ConfigBackup != "" && rollbackCandidate.Python != "" {
				_ = runMaintenance(rollbackCandidate, "config-restore", session.ConfigBackup)
			}
			session.Status, session.Error = "FAILED", resultErr.Error()
			_ = writeUpdateSession(paths, session)
		}
	}()
	versionsRoot := filepath.Join(paths.InstallRoot, "versions")
	if err := os.MkdirAll(versionsRoot, 0o755); err != nil {
		return err
	}
	target := filepath.Join(versionsRoot, manifest.Version)
	if _, err := os.Stat(target); errors.Is(err, os.ErrNotExist) {
		staging := filepath.Join(versionsRoot, ".staging-"+manifest.Version+"-"+fmt.Sprint(time.Now().UnixNano()))
		if err := copyTree(payload, staging); err != nil {
			_ = os.RemoveAll(staging)
			return err
		}
		if _, err := verifyRelease(staging); err != nil {
			_ = os.RemoveAll(staging)
			return err
		}
		if err := os.Rename(staging, target); err != nil {
			_ = os.RemoveAll(staging)
			return err
		}
	} else if err != nil {
		return err
	} else if _, err := verifyRelease(target); err != nil {
		return fmt.Errorf("installed release is invalid: %w", err)
	}
	session.Status = "RELEASE_STAGED"
	if err := writeUpdateSession(paths, session); err != nil {
		return err
	}
	candidate, err := releasePaths(paths, target)
	if err != nil {
		return err
	}
	rollbackCandidate = candidate
	configResult, err := runMaintenanceResult(candidate, "config-upgrade")
	if err != nil {
		return fmt.Errorf("config upgrade rejected; active release unchanged: %w", err)
	}
	if details, ok := configResult["details"].(map[string]any); ok {
		session.ConfigBackup, _ = details["backup"].(string)
	}
	session.Status = "RECOVERY_CREATING"
	if err := writeUpdateSession(paths, session); err != nil {
		return err
	}
	recovery, err := runMaintenanceResult(candidate, "recovery-create")
	if err != nil {
		return fmt.Errorf("recovery set creation failed; active release unchanged: %w", err)
	}
	if details, ok := recovery["details"].(map[string]any); ok {
		session.RecoverySet, _ = details["root"].(string)
	}
	if session.RecoverySet == "" {
		return errors.New("recovery set command did not return a recovery path")
	}
	session.Status = "RECOVERY_COMPLETE"
	if err := writeUpdateSession(paths, session); err != nil {
		return err
	}
	if err := runMaintenance(candidate, "upgrade"); err != nil {
		return fmt.Errorf("database upgrade rejected; active release unchanged: %w", err)
	}
	databaseUpgraded = true
	session.Status = "DATABASE_UPGRADED"
	if err := writeUpdateSession(paths, session); err != nil {
		return err
	}
	session.Status = "CANDIDATE_VERIFYING"
	if err := writeUpdateSession(paths, session); err != nil {
		return err
	}
	if err := smokeCandidate(candidate, machine); err != nil {
		return fmt.Errorf("candidate smoke test failed; active release unchanged: %w", err)
	}
	session.Status = "CANDIDATE_VERIFIED"
	if err := activateRelease(paths.InstallRoot, manifest.Version); err != nil {
		return err
	}
	committed = true
	session.Status = "COMPLETED"
	return writeUpdateSession(paths, session)
}

func materializeBundle(paths hostPaths, bundle string) (string, error) {
	info, err := os.Stat(bundle)
	if err != nil {
		return "", err
	}
	root := bundle
	if !info.IsDir() {
		digest, err := fileSHA256(bundle)
		if err != nil {
			return "", err
		}
		root = filepath.Join(paths.InstanceRoot, "updates", "imports", digest[:16])
		if _, err := os.Stat(root); errors.Is(err, os.ErrNotExist) {
			staging := root + ".partial"
			if err := os.MkdirAll(staging, 0o700); err != nil {
				return "", err
			}
			if err := extractReleaseArchive(bundle, staging); err != nil {
				_ = os.RemoveAll(staging)
				return "", err
			}
			if err := os.Rename(staging, root); err != nil {
				_ = os.RemoveAll(staging)
				return "", err
			}
		} else if err != nil {
			return "", err
		}
	}
	candidates := []string{root, filepath.Join(root, "payload")}
	entries, _ := os.ReadDir(root)
	for _, entry := range entries {
		if entry.IsDir() {
			candidates = append(candidates, filepath.Join(root, entry.Name(), "payload"))
		}
	}
	for _, candidate := range candidates {
		if info, err := os.Stat(filepath.Join(candidate, "release-manifest.json")); err == nil && !info.IsDir() {
			return candidate, nil
		}
	}
	return "", errors.New("release archive does not contain a payload/release-manifest.json")
}

func safeArchiveTarget(root string, name string) (string, error) {
	if strings.HasPrefix(name, "/") || strings.HasPrefix(name, "\\") {
		return "", fmt.Errorf("unsafe archive path %q", name)
	}
	clean := filepath.Clean(filepath.FromSlash(name))
	if clean == "." || filepath.IsAbs(clean) || clean == ".." || strings.HasPrefix(clean, ".."+string(filepath.Separator)) {
		return "", fmt.Errorf("unsafe archive path %q", name)
	}
	target := filepath.Join(root, clean)
	if relative, err := filepath.Rel(root, target); err != nil || relative == ".." || strings.HasPrefix(relative, ".."+string(filepath.Separator)) {
		return "", fmt.Errorf("archive path escapes target: %q", name)
	}
	return target, nil
}

func extractReleaseArchive(archivePath string, targetRoot string) error {
	stream, err := os.Open(archivePath)
	if err != nil {
		return err
	}
	defer stream.Close()
	header := make([]byte, 4)
	if _, err := io.ReadFull(stream, header); err != nil {
		return err
	}
	if _, err := stream.Seek(0, io.SeekStart); err != nil {
		return err
	}
	const maxExpandedBytes int64 = 20 << 30
	var expanded int64
	if header[0] == 'P' && header[1] == 'K' {
		info, err := stream.Stat()
		if err != nil {
			return err
		}
		reader, err := zip.NewReader(stream, info.Size())
		if err != nil {
			return err
		}
		if len(reader.File) > 200000 {
			return errors.New("release archive contains too many files")
		}
		for _, entry := range reader.File {
			expanded += int64(entry.UncompressedSize64)
			if expanded > maxExpandedBytes {
				return errors.New("release archive expands beyond safety limit")
			}
			if entry.Mode()&os.ModeSymlink != 0 {
				return fmt.Errorf("release archive contains a symlink: %s", entry.Name)
			}
			target, err := safeArchiveTarget(targetRoot, entry.Name)
			if err != nil {
				return err
			}
			if entry.FileInfo().IsDir() {
				if err := os.MkdirAll(target, 0o755); err != nil {
					return err
				}
				continue
			}
			if err := os.MkdirAll(filepath.Dir(target), 0o755); err != nil {
				return err
			}
			input, err := entry.Open()
			if err != nil {
				return err
			}
			output, err := os.OpenFile(target, os.O_CREATE|os.O_EXCL|os.O_WRONLY, entry.Mode().Perm())
			if err != nil {
				input.Close()
				return err
			}
			_, copyErr := io.Copy(output, input)
			input.Close()
			closeErr := output.Close()
			if copyErr != nil {
				return copyErr
			}
			if closeErr != nil {
				return closeErr
			}
		}
		return nil
	}
	gzipReader, err := gzip.NewReader(stream)
	if err != nil {
		return errors.New("release archive must be ZIP or tar.gz")
	}
	defer gzipReader.Close()
	tarReader := tar.NewReader(gzipReader)
	count := 0
	for {
		entry, err := tarReader.Next()
		if errors.Is(err, io.EOF) {
			return nil
		}
		if err != nil {
			return err
		}
		count++
		if count > 200000 {
			return errors.New("release archive contains too many files")
		}
		expanded += entry.Size
		if expanded > maxExpandedBytes {
			return errors.New("release archive expands beyond safety limit")
		}
		if entry.Typeflag != tar.TypeReg && entry.Typeflag != tar.TypeRegA && entry.Typeflag != tar.TypeDir {
			return fmt.Errorf("release archive contains unsupported entry: %s", entry.Name)
		}
		target, err := safeArchiveTarget(targetRoot, entry.Name)
		if err != nil {
			return err
		}
		if entry.Typeflag == tar.TypeDir {
			if err := os.MkdirAll(target, os.FileMode(entry.Mode).Perm()); err != nil {
				return err
			}
			continue
		}
		if err := os.MkdirAll(filepath.Dir(target), 0o755); err != nil {
			return err
		}
		output, err := os.OpenFile(target, os.O_CREATE|os.O_EXCL|os.O_WRONLY, os.FileMode(entry.Mode).Perm())
		if err != nil {
			return err
		}
		_, copyErr := io.CopyN(output, tarReader, entry.Size)
		closeErr := output.Close()
		if copyErr != nil {
			return copyErr
		}
		if closeErr != nil {
			return closeErr
		}
	}
}

func smokeCandidate(paths hostPaths, config machineConfig) error {
	port := config.Network.Port
	if port == 0 {
		port = 3210
	}
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	api := pythonCommand(ctx, paths, "local_drama.entrypoints.api", "--host", "127.0.0.1", "--port", strconv.Itoa(port))
	if err := api.Start(); err != nil {
		return err
	}
	if err := waitReady(ctx, port, api, 30*time.Second); err != nil {
		_ = terminate(api, 2*time.Second)
		_ = api.Wait()
		return err
	}
	probeURL := "http://127.0.0.1:" + strconv.Itoa(port) + "/"
	probe := exec.CommandContext(ctx, paths.Python, "-c", "import urllib.request,sys; r=urllib.request.urlopen(sys.argv[1],timeout=5); b=r.read(4096).lower(); sys.exit(0 if r.status==200 and b'<html' in b else 1)", probeURL)
	probe.Env = api.Env
	if err := probe.Run(); err != nil {
		_ = terminate(api, 2*time.Second)
		_ = api.Wait()
		return fmt.Errorf("static web probe failed: %w", err)
	}
	_ = terminate(api, 2*time.Second)
	_ = api.Wait()
	worker := pythonCommand(ctx, paths, "local_drama.entrypoints.worker", "--config", paths.ConfigPath, "--status")
	worker.Stdout = io.Discard
	if err := worker.Run(); err != nil {
		return fmt.Errorf("worker compatibility probe failed: %w", err)
	}
	return nil
}

func writeUpdateSession(paths hostPaths, session *updateSession) error {
	session.UpdatedAt = time.Now().UTC()
	raw, err := json.MarshalIndent(session, "", "  ")
	if err != nil {
		return err
	}
	root := filepath.Join(paths.InstanceRoot, "updates", "sessions", session.SessionID)
	if err := os.MkdirAll(root, 0o700); err != nil {
		return err
	}
	target := filepath.Join(root, "state.json")
	temporary := target + ".partial"
	if err := os.WriteFile(temporary, append(raw, '\n'), 0o600); err != nil {
		return err
	}
	return atomicReplace(temporary, target)
}

func rollbackCLI(args []string) error {
	flags := flag.NewFlagSet("rollback", flag.ContinueOnError)
	config := flags.String("config", "", "machine config path")
	if err := flags.Parse(args); err != nil {
		return err
	}
	paths, _, err := discover(*config)
	if err != nil {
		return err
	}
	if err := resolveRelease(&paths); err != nil {
		return err
	}
	if err := ensureHostStopped(paths); err != nil {
		return err
	}
	active, err := readActiveRelease(paths.InstallRoot)
	if err != nil {
		return err
	}
	if !safeVersion.MatchString(active.Previous) {
		return errors.New("no valid previous release is available")
	}
	previousRoot := filepath.Join(paths.InstallRoot, "versions", active.Previous)
	if _, err := verifyRelease(previousRoot); err != nil {
		return err
	}
	previous, err := releasePaths(paths, previousRoot)
	if err != nil {
		return err
	}
	if err := runMaintenance(previous, "inspect"); err != nil {
		recoverySet, findErr := latestRecoverySet(paths, active.Active)
		if findErr != nil {
			return fmt.Errorf("rollback blocked and no recovery set is available: %w", err)
		}
		current, pathErr := releasePaths(paths, paths.ReleaseRoot)
		if pathErr != nil {
			return pathErr
		}
		if restoreErr := runMaintenance(current, "recovery-restore", recoverySet); restoreErr != nil {
			return fmt.Errorf("rollback recovery restore failed: %w", restoreErr)
		}
		if inspectErr := runMaintenance(previous, "inspect"); inspectErr != nil {
			return fmt.Errorf("previous release remains incompatible after recovery restore: %w", inspectErr)
		}
	}
	active.Active, active.Previous = active.Previous, active.Active
	active.Generation++
	return writeActiveRelease(paths.InstallRoot, active)
}

func ensureHostStopped(paths hostPaths) error {
	lockPath := filepath.Join(paths.RuntimeRoot, "host.lock")
	if _, err := os.Stat(lockPath); errors.Is(err, os.ErrNotExist) {
		return nil
	}
	if err := os.WriteFile(filepath.Join(paths.RuntimeRoot, "host.stop"), []byte("stop\n"), 0o600); err != nil {
		return err
	}
	deadline := time.Now().Add(45 * time.Second)
	for time.Now().Before(deadline) {
		if _, err := os.Stat(lockPath); errors.Is(err, os.ErrNotExist) {
			return nil
		}
		time.Sleep(250 * time.Millisecond)
	}
	return errors.New("running Host did not stop within 45 seconds")
}

func latestRecoverySet(paths hostPaths, version string) (string, error) {
	pattern := filepath.Join(paths.InstanceRoot, "updates", "sessions", "*", "state.json")
	states, err := filepath.Glob(pattern)
	if err != nil {
		return "", err
	}
	var selected updateSession
	for _, statePath := range states {
		raw, readErr := os.ReadFile(statePath)
		if readErr != nil {
			continue
		}
		var session updateSession
		if json.Unmarshal(raw, &session) != nil || session.Version != version || session.RecoverySet == "" {
			continue
		}
		if selected.RecoverySet == "" || session.UpdatedAt.After(selected.UpdatedAt) {
			selected = session
		}
	}
	if selected.RecoverySet == "" {
		return "", errors.New("matching recovery set not found")
	}
	return selected.RecoverySet, nil
}

func readActiveRelease(installRoot string) (activeRelease, error) {
	raw, err := os.ReadFile(filepath.Join(installRoot, "active-release.json"))
	if err != nil {
		return activeRelease{}, err
	}
	var active activeRelease
	if err := json.Unmarshal(raw, &active); err != nil {
		return activeRelease{}, err
	}
	return active, nil
}

func activateRelease(installRoot string, version string) error {
	if !safeVersion.MatchString(version) {
		return errors.New("invalid release version")
	}
	active, err := readActiveRelease(installRoot)
	if errors.Is(err, os.ErrNotExist) {
		active = activeRelease{SchemaVersion: 1}
	} else if err != nil {
		return err
	}
	if active.Active == version {
		return nil
	}
	active.Previous, active.Active = active.Active, version
	active.Generation++
	return writeActiveRelease(installRoot, active)
}

func writeActiveRelease(installRoot string, active activeRelease) error {
	raw, err := json.MarshalIndent(active, "", "  ")
	if err != nil {
		return err
	}
	target := filepath.Join(installRoot, "active-release.json")
	temporary := target + ".partial"
	if err := os.WriteFile(temporary, append(raw, '\n'), 0o644); err != nil {
		return err
	}
	return atomicReplace(temporary, target)
}

func releasePaths(base hostPaths, root string) (hostPaths, error) {
	python := filepath.Join(root, "runtime", "python", "bin", "python3")
	if runtime.GOOS == "windows" {
		python = filepath.Join(root, "runtime", "python", "python.exe")
	}
	if info, err := os.Stat(python); err != nil || info.IsDir() {
		return hostPaths{}, fmt.Errorf("candidate private Python is missing: %s", python)
	}
	identity, err := loadReleaseIdentity(root, root)
	if err != nil {
		return hostPaths{}, fmt.Errorf("candidate release version identity is invalid: %w", err)
	}
	base.ReleaseRoot, base.Version, base.Python, base.AppRoot = root, identity.Version, python, filepath.Join(root, "app")
	base.HostProtocol = identity.HostProtocol
	return base, nil
}

func runMaintenance(paths hostPaths, operation string, args ...string) error {
	_, err := runMaintenanceResult(paths, operation, args...)
	return err
}

func runMaintenanceResult(paths hostPaths, operation string, args ...string) (map[string]any, error) {
	commandArgs := []string{"-m", "local_drama.entrypoints.maintenance", "--config", paths.ConfigPath, operation}
	commandArgs = append(commandArgs, args...)
	command := exec.Command(paths.Python, commandArgs...)
	command.Dir = paths.ReleaseRoot
	command.Env = append(os.Environ(),
		"LOCAL_DRAMA_RELEASE_ROOT="+paths.ReleaseRoot,
		"LOCAL_DRAMA_INSTANCE_ROOT="+paths.InstanceRoot,
		"LOCAL_DRAMA_CONFIG="+paths.ConfigPath,
		"PYTHONPATH="+paths.AppRoot,
		"LOCAL_DRAMA_PACKAGED=1",
	)
	command.Stderr = os.Stderr
	raw, err := command.Output()
	if len(raw) > 0 {
		_, _ = os.Stdout.Write(raw)
	}
	if err != nil {
		return nil, err
	}
	var result map[string]any
	lines := strings.Split(strings.TrimSpace(string(raw)), "\n")
	lastLine := []byte(lines[len(lines)-1])
	if err := json.Unmarshal(lastLine, &result); err != nil {
		return nil, fmt.Errorf("maintenance returned invalid JSON: %w", err)
	}
	return result, nil
}

func copyTree(source string, target string) error {
	return filepath.Walk(source, func(path string, info os.FileInfo, walkErr error) error {
		if walkErr != nil {
			return walkErr
		}
		relative, err := filepath.Rel(source, path)
		if err != nil {
			return err
		}
		destination := filepath.Join(target, relative)
		if info.IsDir() {
			return os.MkdirAll(destination, info.Mode().Perm())
		}
		if !info.Mode().IsRegular() {
			return fmt.Errorf("unsupported release entry: %s", path)
		}
		input, err := os.Open(path)
		if err != nil {
			return err
		}
		output, err := os.OpenFile(destination, os.O_CREATE|os.O_EXCL|os.O_WRONLY, info.Mode().Perm())
		if err != nil {
			input.Close()
			return err
		}
		_, copyErr := io.Copy(output, input)
		inputErr := input.Close()
		closeErr := output.Close()
		if copyErr != nil {
			return copyErr
		}
		if inputErr != nil {
			return inputErr
		}
		return closeErr
	})
}
