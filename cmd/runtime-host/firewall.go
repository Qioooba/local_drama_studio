package main

import (
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"net"
	"os"
	"path/filepath"
	"strings"
)

const firewallRuleName = "LocalDramaStudio LAN"

func configureFirewallCLI(args []string) error {
	flags := flag.NewFlagSet("configure-firewall", flag.ContinueOnError)
	configOverride := flags.String("config", "", "machine config path")
	remoteAddress := flags.String("remote-address", "", "LocalSubnet or a comma-separated IP/CIDR allowlist; persists when provided")
	if err := flags.Parse(args); err != nil {
		return err
	}
	paths, config, err := discover(*configOverride)
	if err != nil {
		return err
	}
	if err := validateMachineNetwork(config); err != nil {
		return err
	}
	if !strings.EqualFold(config.Network.Mode, "LAN_SERVICE") {
		return errors.New("configure-firewall is only valid for LAN_SERVICE")
	}
	effectiveRemoteAddress := strings.TrimSpace(*remoteAddress)
	if effectiveRemoteAddress == "" {
		effectiveRemoteAddress = strings.TrimSpace(config.Network.FirewallRemoteAddress)
	}
	if effectiveRemoteAddress == "" {
		effectiveRemoteAddress = "LocalSubnet"
	}
	normalized, err := validateFirewallRemoteAddress(effectiveRemoteAddress)
	if err != nil {
		return err
	}
	if strings.TrimSpace(*remoteAddress) != "" {
		if _, err := persistFirewallRemoteAddress(paths.ConfigPath, filepath.Join(paths.InstanceRoot, "backups", "config"), normalized); err != nil {
			return fmt.Errorf("persist firewall scope: %w", err)
		}
	}
	if err := installFirewallRule(config.Network.Port, normalized); err != nil {
		return err
	}
	fmt.Printf("configured firewall rule %q port=%d remote=%s\n", firewallRuleName, config.Network.Port, normalized)
	return nil
}

func persistFirewallRemoteAddress(configPath string, backupsRoot string, remoteAddress string) (string, error) {
	normalized, err := validateFirewallRemoteAddress(remoteAddress)
	if err != nil {
		return "", err
	}
	raw, err := os.ReadFile(configPath)
	if err != nil {
		return "", err
	}
	var config map[string]any
	if err := json.Unmarshal(raw, &config); err != nil {
		return "", err
	}
	network, ok := config["network"].(map[string]any)
	if !ok {
		return "", errors.New("machine config must contain a network object")
	}
	if current, _ := network["firewall_remote_address"].(string); current == normalized {
		return "", nil
	}
	backup, err := writeConfigBackup(backupsRoot, "config-before-firewall-scope", raw)
	if err != nil {
		return "", err
	}
	network["firewall_remote_address"] = normalized
	if err := writeJSONAtomic(configPath, config); err != nil {
		return backup, err
	}
	return backup, nil
}

func removeFirewallCLI(args []string) error {
	flags := flag.NewFlagSet("remove-firewall", flag.ContinueOnError)
	if err := flags.Parse(args); err != nil {
		return err
	}
	if err := removeFirewallRule(); err != nil {
		return err
	}
	fmt.Printf("removed firewall rule %q\n", firewallRuleName)
	return nil
}

func validateFirewallRemoteAddress(value string) (string, error) {
	trimmed := strings.TrimSpace(value)
	if strings.EqualFold(trimmed, "LocalSubnet") {
		return "LocalSubnet", nil
	}
	parts := strings.Split(trimmed, ",")
	if len(parts) == 0 {
		return "", errors.New("firewall remote address is empty")
	}
	normalized := make([]string, 0, len(parts))
	for _, part := range parts {
		candidate := strings.TrimSpace(part)
		if candidate == "" {
			return "", errors.New("firewall remote address contains an empty entry")
		}
		if ip := net.ParseIP(candidate); ip != nil {
			normalized = append(normalized, ip.String())
			continue
		}
		_, network, err := net.ParseCIDR(candidate)
		if err != nil {
			return "", fmt.Errorf("invalid firewall remote address %q", candidate)
		}
		normalized = append(normalized, network.String())
	}
	return strings.Join(normalized, ","), nil
}

func firewallAddArguments(port int, remoteAddress string) ([]string, error) {
	if port < 1 || port > 65535 {
		return nil, fmt.Errorf("invalid firewall port %d", port)
	}
	remote, err := validateFirewallRemoteAddress(remoteAddress)
	if err != nil {
		return nil, err
	}
	return []string{
		"advfirewall", "firewall", "add", "rule",
		"name=" + firewallRuleName,
		"dir=in",
		"action=allow",
		"protocol=TCP",
		fmt.Sprintf("localport=%d", port),
		"remoteip=" + remote,
		"profile=any",
		"edge=no",
		"enable=yes",
	}, nil
}
