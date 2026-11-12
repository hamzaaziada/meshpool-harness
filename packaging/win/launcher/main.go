// Meshpool.exe: starts the Meshpool app (bundled Python, no console) and exits.
package main

import (
	"os"
	"os/exec"
	"path/filepath"
	"syscall"
	"unsafe"
)

func msg(text string) {
	u := syscall.NewLazyDLL("user32.dll").NewProc("MessageBoxW")
	t, _ := syscall.UTF16PtrFromString(text)
	c, _ := syscall.UTF16PtrFromString("Meshpool")
	u.Call(0, uintptr(unsafe.Pointer(t)), uintptr(unsafe.Pointer(c)), 0x10)
}

func main() {
	exe, err := os.Executable()
	if err != nil {
		msg("Meshpool could not find its own folder.")
		return
	}
	dir := filepath.Dir(exe)
	py := filepath.Join(dir, "runtime", "pythonw.exe")
	if _, err := os.Stat(py); err != nil {
		msg("Meshpool is missing files (" + py + "). Please reinstall Meshpool.")
		return
	}
	appdata := os.Getenv("APPDATA")
	if appdata == "" {
		appdata = filepath.Join(os.Getenv("USERPROFILE"), "AppData", "Roaming")
	}
	home := filepath.Join(appdata, "Meshpool")
	os.MkdirAll(filepath.Join(home, "runs"), 0o755)
	logf, _ := os.OpenFile(filepath.Join(home, "runs", "app.log"), os.O_CREATE|os.O_APPEND|os.O_WRONLY, 0o644)

	cmd := exec.Command(py, "-m", "meshpool_harness", "app")
	cmd.Dir = dir
	env := os.Environ()
	env = append(env,
		"MESHPOOL_HOME="+home,
		"PYTHONPATH="+filepath.Join(dir, "app"),
		"PYTHONUTF8=1", "PYTHONIOENCODING=utf-8", "PYTHONNOUSERSITE=1",
		"PYTHONDONTWRITEBYTECODE=1",
		"MESHPOOL_CLI="+filepath.Join(dir, "meshpool.cmd"),
		"PATH="+filepath.Join(dir, "runtime")+";"+filepath.Join(dir, "runtime", "Scripts")+";"+os.Getenv("PATH"),
	)
	cmd.Env = env
	if logf != nil {
		cmd.Stdout, cmd.Stderr = logf, logf
	}
	cmd.SysProcAttr = &syscall.SysProcAttr{HideWindow: true, CreationFlags: 0x08000000 | 0x00000200}
	if err := cmd.Start(); err != nil {
		msg("Meshpool could not start: " + err.Error())
		return
	}
}
