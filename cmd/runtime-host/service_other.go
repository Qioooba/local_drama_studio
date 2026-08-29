//go:build !windows

package main

import (
	"fmt"
	"os"
	"os/exec"
	"syscall"
)

func runService() error {
	return fmt.Errorf("service command is Windows-only; use run under systemd")
}

func installService() error {
	return fmt.Errorf("install-service is Windows-only; install the systemd unit on Linux")
}

func uninstallService() error {
	return fmt.Errorf("uninstall-service is Windows-only; remove the systemd unit on Linux")
}

func installFirewallRule(_ int, _ string) error {
	return fmt.Errorf("configure-firewall is Windows-only; configure the host firewall for the systemd service")
}

func removeFirewallRule() error {
	return fmt.Errorf("remove-firewall is Windows-only; configure the host firewall for the systemd service")
}

func atomicReplace(source string, target string) error {
	return os.Rename(source, target)
}

func processExists(pid int) bool {
	err := syscall.Kill(pid, 0)
	return err == nil || err == syscall.EPERM
}

func configureChildProcess(command *exec.Cmd) {
	_ = command
}

func signalChildProcess(command *exec.Cmd) error {
	return command.Process.Signal(os.Interrupt)
}
