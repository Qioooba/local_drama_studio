package main

import (
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"strings"
)

// releaseIdentity mirrors release/version.json, the single source of version
// truth shared with the Python runtime. The Host must never invent a product
// version itself: it resolves the same file the API and Worker resolve
// (local_drama.bootstrap.build_identity / ResourceLocator.version_path).
type releaseIdentity struct {
	Version              string `json:"version"`
	Channel              string `json:"channel"`
	HostProtocol         int    `json:"host_protocol"`
	WorkerProtocol       int    `json:"worker_protocol"`
	ConfigSchema         int    `json:"config_schema"`
	ProjectPackageSchema int    `json:"project_package_schema"`
}

// Protocol versions this Host implementation can supervise.
const (
	hostProtocolSupported   = 1
	workerProtocolSupported = 1
)

// loadReleaseIdentity resolves release/version.json exactly like the Python
// ResourceLocator: <releaseRoot>/version.json for packaged payloads, falling
// back to <repositoryRoot>/release/version.json for source checkouts. The
// returned identity is validated so a broken or drifted identity fails at
// startup with an actionable error instead of surfacing as a worker
// handshake crash loop.
func loadReleaseIdentity(releaseRoot string, repositoryRoot string) (releaseIdentity, error) {
	candidates := []string{
		filepath.Join(releaseRoot, "version.json"),
		filepath.Join(repositoryRoot, "release", "version.json"),
	}
	for _, candidate := range candidates {
		raw, err := os.ReadFile(candidate)
		if err != nil {
			if errors.Is(err, os.ErrNotExist) {
				continue
			}
			return releaseIdentity{}, err
		}
		identity, err := parseReleaseIdentity(raw)
		if err != nil {
			return releaseIdentity{}, fmt.Errorf("invalid release version identity %s: %w", candidate, err)
		}
		return identity, nil
	}
	return releaseIdentity{}, fmt.Errorf("release version identity not found (checked %s)", strings.Join(candidates, ", "))
}

func parseReleaseIdentity(raw []byte) (releaseIdentity, error) {
	var identity releaseIdentity
	if err := json.Unmarshal(raw, &identity); err != nil {
		return releaseIdentity{}, err
	}
	if !safeVersion.MatchString(identity.Version) {
		return releaseIdentity{}, fmt.Errorf("version %q is invalid", identity.Version)
	}
	if identity.Channel == "" {
		return releaseIdentity{}, errors.New("channel is empty")
	}
	if identity.HostProtocol != hostProtocolSupported {
		return releaseIdentity{}, fmt.Errorf("host protocol %d is not supported (want %d)", identity.HostProtocol, hostProtocolSupported)
	}
	if identity.WorkerProtocol != workerProtocolSupported {
		return releaseIdentity{}, fmt.Errorf("worker protocol %d is not supported (want %d)", identity.WorkerProtocol, workerProtocolSupported)
	}
	if identity.ConfigSchema < 1 || identity.ProjectPackageSchema < 1 {
		return releaseIdentity{}, errors.New("config/project package schema must be at least 1")
	}
	return identity, nil
}
