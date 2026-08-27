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

	"golang.org/x/sys/windows"
)

type launcherConfig struct {
	Network struct {
		Port int `json:"port"`
	} `json:"network"`
}

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
	url := "http://127.0.0.1:" + strconv.Itoa(port) + "/api/v1/health/live"
	if !healthy(url) {
		command := exec.Command(host, "run")
		command.Dir = filepath.Dir(filepath.Dir(host))
		command.SysProcAttr = &syscall.SysProcAttr{
			HideWindow:    true,
			CreationFlags: windows.CREATE_NEW_PROCESS_GROUP | windows.DETACHED_PROCESS,
		}
		if err := command.Start(); err != nil {
			return err
		}
		if err := command.Process.Release(); err != nil {
			return err
		}
		deadline := time.Now().Add(60 * time.Second)
		for time.Now().Before(deadline) && !healthy(url) {
			time.Sleep(250 * time.Millisecond)
		}
		if !healthy(url) {
			return fmt.Errorf("API did not become healthy on port %d", port)
		}
	}
	page := "http://127.0.0.1:" + strconv.Itoa(port) + "/"
	opener := exec.Command("rundll32.exe", "url.dll,FileProtocolHandler", page)
	opener.SysProcAttr = &syscall.SysProcAttr{HideWindow: true}
	return opener.Start()
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
