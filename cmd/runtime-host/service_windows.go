//go:build windows

package main

import (
	"context"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"syscall"
	"time"

	"golang.org/x/sys/windows"
	"golang.org/x/sys/windows/svc"
	"golang.org/x/sys/windows/svc/mgr"
)

const serviceName = "LocalDramaStudio"

type windowsService struct{}

func (windowsService) Execute(_ []string, requests <-chan svc.ChangeRequest, changes chan<- svc.Status) (bool, uint32) {
	const accepted = svc.AcceptStop | svc.AcceptShutdown | svc.AcceptPreShutdown
	changes <- svc.Status{State: svc.StartPending}
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	done := make(chan error, 1)
	go func() { done <- supervise(ctx, "") }()
	changes <- svc.Status{State: svc.Running, Accepts: accepted}
	for {
		select {
		case request := <-requests:
			switch request.Cmd {
			case svc.Interrogate:
				changes <- svc.Status{State: svc.Running, Accepts: accepted}
			case svc.Stop, svc.Shutdown, svc.PreShutdown:
				changes <- svc.Status{State: svc.StopPending}
				cancel()
				select {
				case <-done:
				case <-time.After(30 * time.Second):
				}
				return false, 0
			}
		case err := <-done:
			if err != nil {
				return true, 1
			}
			return false, 0
		}
	}
}

func runService() error {
	isService, err := svc.IsWindowsService()
	if err != nil {
		return err
	}
	if !isService {
		return fmt.Errorf("service command must be started by Windows Service Control Manager")
	}
	return svc.Run(serviceName, windowsService{})
}

func installService() error {
	executable, err := os.Executable()
	if err != nil {
		return err
	}
	executable, err = filepath.Abs(executable)
	if err != nil {
		return err
	}
	manager, err := mgr.Connect()
	if err != nil {
		return err
	}
	defer manager.Disconnect()
	configuration := mgr.Config{
		StartType:        mgr.StartAutomatic,
		DisplayName:      "Local Drama Studio",
		Description:      "Local Drama Studio runtime supervisor, API and background worker",
		DelayedAutoStart: true,
	}
	if existing, openErr := manager.OpenService(serviceName); openErr == nil {
		defer existing.Close()
		existingConfiguration, err := existing.Config()
		if err != nil {
			return err
		}
		existingConfiguration.StartType = configuration.StartType
		existingConfiguration.BinaryPathName = fmt.Sprintf("\"%s\" service", executable)
		existingConfiguration.DisplayName = configuration.DisplayName
		existingConfiguration.Description = configuration.Description
		existingConfiguration.DelayedAutoStart = configuration.DelayedAutoStart
		if err := existing.UpdateConfig(existingConfiguration); err != nil {
			return err
		}
		return existing.SetRecoveryActions([]mgr.RecoveryAction{
			{Type: mgr.ServiceRestart, Delay: 5 * time.Second},
			{Type: mgr.ServiceRestart, Delay: 15 * time.Second},
			{Type: mgr.ServiceRestart, Delay: 30 * time.Second},
		}, 24*60*60)
	}
	service, err := manager.CreateService(serviceName, executable, configuration, "service")
	if err != nil {
		return err
	}
	defer service.Close()
	return service.SetRecoveryActions([]mgr.RecoveryAction{
		{Type: mgr.ServiceRestart, Delay: 5 * time.Second},
		{Type: mgr.ServiceRestart, Delay: 15 * time.Second},
		{Type: mgr.ServiceRestart, Delay: 30 * time.Second},
	}, 24*60*60)
}

func uninstallService() error {
	manager, err := mgr.Connect()
	if err != nil {
		return err
	}
	defer manager.Disconnect()
	service, err := manager.OpenService(serviceName)
	if err != nil {
		return nil
	}
	defer service.Close()
	_, _ = service.Control(svc.Stop)
	return service.Delete()
}

func netshPath() string {
	if systemRoot := os.Getenv("SystemRoot"); systemRoot != "" {
		candidate := filepath.Join(systemRoot, "System32", "netsh.exe")
		if _, err := os.Stat(candidate); err == nil {
			return candidate
		}
	}
	return "netsh.exe"
}

func installFirewallRule(port int, remoteAddress string) error {
	arguments, err := firewallAddArguments(port, remoteAddress)
	if err != nil {
		return err
	}
	_ = removeFirewallRule()
	command := exec.Command(netshPath(), arguments...)
	command.SysProcAttr = &syscall.SysProcAttr{HideWindow: true}
	output, err := command.CombinedOutput()
	if err != nil {
		return fmt.Errorf("configure Windows Firewall: %w (%s)", err, strings.TrimSpace(string(output)))
	}
	return nil
}

func removeFirewallRule() error {
	command := exec.Command(netshPath(), "advfirewall", "firewall", "delete", "rule", "name="+firewallRuleName)
	command.SysProcAttr = &syscall.SysProcAttr{HideWindow: true}
	output, err := command.CombinedOutput()
	if err != nil {
		text := strings.ToLower(string(output))
		if strings.Contains(text, "no rules match") || strings.Contains(text, "找不到") {
			return nil
		}
		return fmt.Errorf("remove Windows Firewall rule: %w (%s)", err, strings.TrimSpace(string(output)))
	}
	return nil
}

func atomicReplace(source string, target string) error {
	sourcePointer, err := windows.UTF16PtrFromString(source)
	if err != nil {
		return err
	}
	targetPointer, err := windows.UTF16PtrFromString(target)
	if err != nil {
		return err
	}
	return windows.MoveFileEx(sourcePointer, targetPointer, windows.MOVEFILE_REPLACE_EXISTING|windows.MOVEFILE_WRITE_THROUGH)
}

func processExists(pid int) bool {
	handle, err := windows.OpenProcess(windows.PROCESS_QUERY_LIMITED_INFORMATION, false, uint32(pid))
	if err == nil {
		windows.CloseHandle(handle)
		return true
	}
	return err != windows.ERROR_INVALID_PARAMETER
}

func configureChildProcess(command *exec.Cmd) {
	command.SysProcAttr = &syscall.SysProcAttr{
		HideWindow:    true,
		CreationFlags: windows.CREATE_NEW_PROCESS_GROUP,
	}
}

func signalChildProcess(command *exec.Cmd) error {
	return windows.GenerateConsoleCtrlEvent(windows.CTRL_BREAK_EVENT, uint32(command.Process.Pid))
}
