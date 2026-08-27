package main

import (
	"crypto/ed25519"
	"crypto/rand"
	"crypto/sha256"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"os"
	"path/filepath"
)

func main() {
	if err := run(os.Args[1:]); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
}

func run(args []string) error {
	if len(args) == 0 {
		return errors.New("expected keygen or sign")
	}
	switch args[0] {
	case "keygen":
		flags := flag.NewFlagSet("keygen", flag.ContinueOnError)
		privatePath := flags.String("private", "", "private key output")
		publicPath := flags.String("public", "", "public key output")
		if err := flags.Parse(args[1:]); err != nil {
			return err
		}
		if *privatePath == "" || *publicPath == "" {
			return errors.New("--private and --public are required")
		}
		publicKey, privateKey, err := ed25519.GenerateKey(rand.Reader)
		if err != nil {
			return err
		}
		if err := writeExclusive(*privatePath, []byte(base64.StdEncoding.EncodeToString(privateKey)+"\n"), 0o600); err != nil {
			return err
		}
		return writeExclusive(*publicPath, []byte(base64.StdEncoding.EncodeToString(publicKey)+"\n"), 0o644)
	case "sign":
		flags := flag.NewFlagSet("sign", flag.ContinueOnError)
		manifestPath := flags.String("manifest", "", "release manifest path")
		privatePath := flags.String("private", "", "base64 private key path")
		if err := flags.Parse(args[1:]); err != nil {
			return err
		}
		if *manifestPath == "" || *privatePath == "" {
			return errors.New("--manifest and --private are required")
		}
		return signManifest(*manifestPath, *privatePath)
	default:
		return fmt.Errorf("unknown command %q", args[0])
	}
}

func signManifest(manifestPath string, privatePath string) error {
	encoded, err := os.ReadFile(privatePath)
	if err != nil {
		return err
	}
	privateKey, err := base64.StdEncoding.DecodeString(string(bytesTrimSpace(encoded)))
	if err != nil || len(privateKey) != ed25519.PrivateKeySize {
		return errors.New("private key is invalid")
	}
	raw, err := os.ReadFile(manifestPath)
	if err != nil {
		return err
	}
	var manifest map[string]any
	if err := json.Unmarshal(raw, &manifest); err != nil {
		return err
	}
	manifest["signature"] = nil
	canonical, err := json.Marshal(manifest)
	if err != nil {
		return err
	}
	signature := ed25519.Sign(ed25519.PrivateKey(privateKey), canonical)
	publicKey := ed25519.PrivateKey(privateKey).Public().(ed25519.PublicKey)
	digest := sha256.Sum256(publicKey)
	manifest["signature"] = map[string]any{
		"algorithm": "Ed25519",
		"key_id":    hex.EncodeToString(digest[:8]),
		"value":     base64.StdEncoding.EncodeToString(signature),
	}
	pretty, err := json.MarshalIndent(manifest, "", "  ")
	if err != nil {
		return err
	}
	temporary := manifestPath + ".partial"
	if err := os.WriteFile(temporary, append(pretty, '\n'), 0o644); err != nil {
		return err
	}
	if err := os.Remove(manifestPath); err != nil {
		return err
	}
	return os.Rename(temporary, manifestPath)
}

func writeExclusive(path string, value []byte, mode os.FileMode) error {
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil && filepath.Dir(path) != "." {
		return err
	}
	file, err := os.OpenFile(path, os.O_CREATE|os.O_EXCL|os.O_WRONLY, mode)
	if err != nil {
		return err
	}
	if _, err := file.Write(value); err != nil {
		file.Close()
		return err
	}
	return file.Close()
}

func bytesTrimSpace(value []byte) []byte {
	start, end := 0, len(value)
	for start < end && (value[start] == ' ' || value[start] == '\n' || value[start] == '\r' || value[start] == '\t') {
		start++
	}
	for end > start && (value[end-1] == ' ' || value[end-1] == '\n' || value[end-1] == '\r' || value[end-1] == '\t') {
		end--
	}
	return value[start:end]
}
