//go:build windows

package main

import (
	"encoding/json"
	"fmt"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
	"strconv"
	"syscall"
	"time"
	"unsafe"

	"golang.org/x/sys/windows"
)

type launcherConfig struct {
	Network struct {
		Port int `json:"port"`
	} `json:"network"`
}

const (
	messageBoxYesNoCancel  = 0x00000003
	messageBoxIconQuestion = 0x00000020
	messageBoxTopmost      = 0x00040000
	messageBoxResultYes    = 6
	messageBoxResultNo     = 7
)

func main() {
	if err := launch(); err != nil {
		_, _ = fmt.Fprintln(os.Stderr, err)
		message := exec.Command("msg.exe", "*", "/TIME:15", "Local Drama Studio failed to start: "+err.Error())
		message.SysProcAttr = &syscall.SysProcAttr{HideWindow: true}
		_ = message.Run()
		os.Exit(1)
	}
}

func launch() error {
	executable, err := os.Executable()
	if err != nil {
		return err
	}
	host := filepath.Join(filepath.Dir(executable), "local-drama-host.exe")
	if _, err := os.Stat(host); err != nil {
		return err
	}
	port := 3210
	configPath := os.Getenv("LOCAL_DRAMA_CONFIG")
	if configPath == "" {
		instanceRoot := os.Getenv("LOCAL_DRAMA_INSTANCE_ROOT")
		if instanceRoot == "" {
			instanceRoot = filepath.Join(os.Getenv("PROGRAMDATA"), "LocalDramaStudio")
		}
		configPath = filepath.Join(instanceRoot, "config", "config.json")
	}
	if raw, err := os.ReadFile(configPath); err == nil {
		var config launcherConfig
		if json.Unmarshal(raw, &config) == nil && config.Network.Port > 0 {
			port = config.Network.Port
		}
	}
	healthURL := "http://127.0.0.1:" + strconv.Itoa(port) + "/api/v1/health/live"
	installRoot := discoverInstallRoot(executable)
	if healthy(healthURL) {
		action := runningHostAction()
		if action == 0 {
			return nil
		}
		if action == messageBoxResultYes {
			stop := exec.Command(host, "stop")
			stop.Dir = installRoot
			stop.SysProcAttr = &syscall.SysProcAttr{HideWindow: true}
			if err := stop.Run(); err != nil {
				return fmt.Errorf("failed to stop the existing LocalDramaStudio Host: %w", err)
			}
			deadline := time.Now().Add(20 * time.Second)
			for time.Now().Before(deadline) && healthy(healthURL) {
				time.Sleep(250 * time.Millisecond)
			}
			if healthy(healthURL) {
				return fmt.Errorf("existing API did not stop on port %d", port)
			}
		}
		if action != messageBoxResultYes && action != messageBoxResultNo {
			return nil
		}
	}
	if !healthy(healthURL) {
		if err := startHost(host, installRoot); err != nil {
			return err
		}
		deadline := time.Now().Add(60 * time.Second)
		for time.Now().Before(deadline) && !healthy(healthURL) {
			time.Sleep(250 * time.Millisecond)
		}
		if !healthy(healthURL) {
			return fmt.Errorf("API did not become healthy on port %d", port)
		}
	}
	page := "http://127.0.0.1:" + strconv.Itoa(port) + "/"
	opener := exec.Command("rundll32.exe", "url.dll,FileProtocolHandler", page)
	opener.SysProcAttr = &syscall.SysProcAttr{HideWindow: true}
	return opener.Start()
}

func discoverInstallRoot(executable string) string {
	candidate := filepath.Dir(executable)
	for range 5 {
		if _, err := os.Stat(filepath.Join(candidate, "config", "config.json")); err == nil {
			return candidate
		}
		parent := filepath.Dir(candidate)
		if parent == candidate {
			break
		}
		candidate = parent
	}
	return filepath.Dir(filepath.Dir(executable))
}

func startHost(host string, installRoot string) error {
	command := exec.Command(host, "run")
	command.Dir = installRoot
	command.Env = append(os.Environ(),
		"LOCAL_DRAMA_INSTALL_ROOT="+installRoot,
		"LOCAL_DRAMA_INSTANCE_ROOT="+installRoot,
		"LOCAL_DRAMA_PYTHON="+filepath.Join(installRoot, ".venv", "Scripts", "python.exe"),
	)
	command.SysProcAttr = &syscall.SysProcAttr{
		HideWindow:    true,
		CreationFlags: windows.CREATE_NEW_PROCESS_GROUP | windows.DETACHED_PROCESS,
	}
	if err := command.Start(); err != nil {
		return err
	}
	return command.Process.Release()
}

func runningHostAction() int {
	title, _ := windows.UTF16PtrFromString("LocalDramaStudio")
	message, _ := windows.UTF16PtrFromString("LocalDramaStudio 已在运行。\n\n是：重启并应用最新程序更新\n否：仅打开当前页面\n取消：保持不变")
	result, _, _ := windows.NewLazySystemDLL("user32.dll").NewProc("MessageBoxW").Call(
		0,
		uintptr(unsafe.Pointer(message)),
		uintptr(unsafe.Pointer(title)),
		messageBoxYesNoCancel|messageBoxIconQuestion|messageBoxTopmost,
	)
	return int(result)
}

func healthy(url string) bool {
	client := &http.Client{Timeout: 2 * time.Second}
	response, err := client.Get(url)
	if err != nil {
		return false
	}
	defer response.Body.Close()
	return response.StatusCode == http.StatusOK
}
