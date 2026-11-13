from meshpool_harness.ui import workbench as W


def test_windows_terminal_shells(monkeypatch, tmp_path):
    bash = tmp_path / "bash.exe"
    bash.write_text("")
    monkeypatch.setenv("MH_BASH", str(bash))
    ps = W._shell_cmd("Get-ChildItem", "powershell", nt=True)
    assert ps[0] == "powershell" and ps[-1].endswith("Get-ChildItem") and "-NoProfile" in ps
    assert W._shell_cmd("dir", "cmd", nt=True)[:2] == ["cmd", "/d"]
    assert W._shell_cmd("ls", "bash", nt=True) == [str(bash), "-lc", "ls"]
    assert W._shell_cmd("Get-Date", "", nt=True)[0] == "powershell"          # PowerShell is the default on Windows


def test_mac_and_linux_use_the_login_shell(monkeypatch):
    monkeypatch.setenv("SHELL", "/bin/bash")
    assert W._shell_cmd("echo hi", nt=False) == ["/bin/bash", "-lc", "echo hi"]
