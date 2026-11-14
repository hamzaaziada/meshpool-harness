import io
import json
import tarfile

from meshpool_harness import updater as U


def test_version_compare():
    assert U._ver("v2.10.0") > U._ver("2.9.9")
    assert not U._ver("v2.6.0") > U._ver("2.6.0")


def test_check_reports_newer(monkeypatch):
    body = json.dumps({"tag_name": "v0.99.0", "html_url": "u", "assets": [{"name": "Meshpool.dmg", "browser_download_url": "d"}]})
    monkeypatch.setattr(U, "_get", lambda *a, **k: io.BytesIO(body.encode()))
    U._CACHE.clear()
    v = U.check(force=True)
    assert v["ok"] and v["newer"] and v["latest"] == "0.99.0"


def test_check_offline_is_a_message_not_a_crash(monkeypatch):
    def boom(*a, **k):
        raise OSError("offline")
    monkeypatch.setattr(U, "_get", boom)
    v = U.check(force=True)
    assert not v["ok"] and "GitHub" in v["error"]


def test_dev_checkout_is_never_overwritten(monkeypatch):
    monkeypatch.setattr(U, "check", lambda force=False: {"ok": True, "newer": True, "kind": "dev", "current": "1",
                                                         "latest": "2", "tag": "v2", "assets": [], "url": ""})
    r = U.install()
    assert not r["ok"] and "git pull" in r["error"]


def test_install_replaces_code_and_keeps_vendor(monkeypatch, tmp_path):
    app = tmp_path / "app"
    (app / "meshpool_harness").mkdir(parents=True)
    (app / "meshpool_harness" / "__init__.py").write_text('__version__ = "1.0.0"\n')
    (app / "vendor").mkdir()
    (app / "vendor" / "keep").write_text("k")
    (app / "requirements.txt").write_text("x\n")
    rel = tmp_path / "rel" / "meshpool-harness-2.0.0" / "app" / "meshpool_harness"
    rel.mkdir(parents=True)
    (rel / "__init__.py").write_text('__version__ = "2.0.0"\n')
    (rel / "new.py").write_text("")
    (rel.parent / "requirements.txt").write_text("x\n")
    tgz = tmp_path / "src.tgz"
    with tarfile.open(tgz, "w:gz") as t:
        t.add(tmp_path / "rel" / "meshpool-harness-2.0.0", arcname="meshpool-harness-2.0.0")
    monkeypatch.setattr(U, "APP", app)
    monkeypatch.setattr(U, "SRC", tgz.as_uri())
    monkeypatch.setattr(U, "check", lambda force=False: {"ok": True, "newer": True, "kind": "cli", "current": "1.0.0",
                                                         "latest": "2.0.0", "tag": "v2.0.0", "assets": [], "url": ""})
    monkeypatch.setattr(U, "_download", lambda url, dst: __import__("shutil").copy(tgz, dst) and dst)
    r = U.install()
    assert r["ok"] and r["action"] == "restart", r
    assert '2.0.0' in (app / "meshpool_harness" / "__init__.py").read_text()
    assert (app / "meshpool_harness" / "new.py").exists()
    assert (app / "vendor" / "keep").read_text() == "k"


def test_old_2x_releases_are_not_updates_for_the_beta(monkeypatch):
    rels = [{"tag_name": "v2.6.0"}, {"tag_name": "v0.1.0"}, {"tag_name": "v0.2.0", "draft": True}]
    monkeypatch.setattr(U, "_get", lambda *a, **k: io.BytesIO(json.dumps(rels).encode()))
    monkeypatch.setattr(U.MH, "__version__", "0.1.0")
    U._CACHE.clear()
    v = U.check(force=True)
    assert v["ok"] and not v["newer"], v


def _release(tmp_path, ver, files):
    """A release source tarball like GitHub's: meshpool-harness-VER/... with the given {relative path: text}."""
    root = tmp_path / "rel" / f"meshpool-harness-{ver}"
    for rel, text in {"app/meshpool_harness/__init__.py": f'__version__ = "{ver}"\n', **files}.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text)
    tgz = tmp_path / "src.tgz"
    with tarfile.open(tgz, "w:gz") as t:
        t.add(root, arcname=root.name)
    return tgz


def _installed(tmp_path, ver="0.1.0"):
    app = tmp_path / "inst" / "app"
    (app / "meshpool_harness").mkdir(parents=True)
    (app / "meshpool_harness" / "__init__.py").write_text(f'__version__ = "{ver}"\n')
    (app / "engines").mkdir()
    (app / "engines" / "old.py").write_text("old")
    return app


def _update_to(monkeypatch, app, tgz, ver, k="cli"):
    monkeypatch.setattr(U, "APP", app)
    monkeypatch.setattr(U, "check", lambda force=False: {"ok": True, "newer": True, "kind": k, "current": "0.1.0",
                                                         "latest": ver, "tag": f"v{ver}", "assets": [], "url": "u"})
    monkeypatch.setattr(U, "_download", lambda url, dst: __import__("shutil").copy(tgz, dst) and dst)


def test_cli_install_with_its_own_python_is_not_the_desktop_app(monkeypatch, tmp_path):
    # install.sh downloads Python into ~/.meshpool-cli/python when the computer has none: still the command line.
    app = tmp_path / ".meshpool-cli" / "app"
    app.mkdir(parents=True)
    (app.parent / "python").mkdir()
    (app.parent / "venv").mkdir()
    monkeypatch.setattr(U, "APP", app)
    monkeypatch.setattr(U.C, "packaged", lambda: True)
    monkeypatch.setattr(U.sys, "platform", "darwin")
    assert U.kind() == "cli"


def test_mac_app_is_still_the_mac_app(monkeypatch, tmp_path):
    app = tmp_path / "Meshpool.app" / "Contents" / "Resources" / "app"
    app.mkdir(parents=True)
    (app.parent / "python").mkdir()
    monkeypatch.setattr(U, "APP", app)
    monkeypatch.setattr(U.C, "packaged", lambda: True)
    monkeypatch.setattr(U.sys, "platform", "darwin")
    assert U.kind() == "mac-app"


def test_pick_offers_only_newer_final_releases_of_this_line():
    rels = [{"tag_name": "v2.6.0", "published_at": "2025-01-01T00:00:00Z"},
            {"tag_name": "v0.0.9", "published_at": "2026-12-01T00:00:00Z"},
            {"tag_name": "v0.3.0-rc.1", "prerelease": True, "published_at": "2026-12-03T00:00:00Z"},
            {"tag_name": "v0.4.0", "prerelease": True, "published_at": "2026-12-04T00:00:00Z"},
            {"tag_name": "v0.2.0", "published_at": "2026-12-02T00:00:00Z"},
            {"tag_name": "v0.5.0", "draft": True}]
    assert U._pick(rels, "0.1.0")["tag_name"] == "v0.2.0"
    assert U._pick(rels, "0.2.0") is None                      # nothing lower, no prerelease, no 2.x
    assert U._pick(rels[:2], "0.1.0") is None                   # 0.0.9 is older, 2.6.0 is the old line


def test_pick_offers_prereleases_to_a_prerelease():
    rels = [{"tag_name": "v0.3.0-rc.1", "prerelease": True}, {"tag_name": "v0.3.0-rc.2", "prerelease": True},
            {"tag_name": "v0.2.0"}]
    assert U._pick(rels, "0.3.0rc1")["tag_name"] == "v0.3.0-rc.2"
    assert U._pick(rels + [{"tag_name": "v0.3.0"}], "0.3.0rc2")["tag_name"] == "v0.3.0"
    assert U._pick(rels, "0.2.0") is None


def test_old_line_is_allowed_again_only_when_published_after_the_renumbering():
    rels = [{"tag_name": "v2.6.0", "published_at": "2025-01-01T00:00:00Z"},
            {"tag_name": "v0.1.0", "published_at": "2026-11-20T00:00:00Z"},
            {"tag_name": "v1.4.0", "published_at": "2027-06-01T00:00:00Z"}]
    assert U._pick(rels, "1.4.0") is None
    assert U._pick(rels + [{"tag_name": "v2.0.0", "published_at": "2028-01-01T00:00:00Z"}], "1.4.0")["tag_name"] == "v2.0.0"


def _installer_setup(monkeypatch, tmp_path, payload, sums):
    import hashlib
    monkeypatch.setattr(U.Path, "home", lambda: tmp_path)
    (tmp_path / "Downloads").mkdir()
    opened = []
    monkeypatch.setattr(U, "_open", opened.append)
    monkeypatch.setattr(U.sys, "platform", "darwin")
    monkeypatch.setattr(U.platform, "machine", lambda: "arm64")
    monkeypatch.setattr(U, "_download", lambda url, dst: dst.write_bytes(payload) and dst)
    body = "".join(f"{hashlib.sha256(d).hexdigest()}  {n}\n" for n, d in sums.items()).encode()
    monkeypatch.setattr(U, "_get", lambda *a, **k: io.BytesIO(body))
    v = {"latest": "0.2.0", "url": "u", "assets": [{"name": "Meshpool.dmg", "url": "d"},
                                                    {"name": "SHA256SUMS.txt", "url": "s"}]}
    return v, opened


def test_installer_download_is_checked_against_the_release_sums(monkeypatch, tmp_path):
    v, opened = _installer_setup(monkeypatch, tmp_path, b"good dmg", {"Meshpool.dmg": b"good dmg"})
    r = U._installer(v, "")
    assert r["ok"] and opened and opened[0].name == "Meshpool.dmg"
    assert (tmp_path / "Downloads" / "Meshpool.dmg").read_bytes() == b"good dmg"


def test_installer_download_that_does_not_match_is_deleted(monkeypatch, tmp_path):
    v, opened = _installer_setup(monkeypatch, tmp_path, b"tampered", {"Meshpool.dmg": b"good dmg"})
    r = U._installer(v, "")
    assert not r["ok"] and "checksum" in r["error"] and not opened
    assert not list((tmp_path / "Downloads").iterdir())


def test_installer_not_listed_in_the_sums_is_refused(monkeypatch, tmp_path):
    v, opened = _installer_setup(monkeypatch, tmp_path, b"x", {"Other.exe": b"x"})
    r = U._installer(v, "")
    assert not r["ok"] and not opened


def test_cli_update_installs_the_command_line_package_list(monkeypatch, tmp_path):
    app = _installed(tmp_path)
    (app / "requirements-cli.txt").write_text("pandas\n")
    tgz = _release(tmp_path, "0.2.0", {"packaging/requirements-cli.txt": "pandas\nnewdep\n",
                                       "app/requirements.txt": "pandas\n"})
    _update_to(monkeypatch, app, tgz, "0.2.0")
    ran = []
    monkeypatch.setattr(U.subprocess, "run", lambda cmd, **k: ran.append(cmd) or
                        __import__("subprocess").CompletedProcess(cmd, 0, "", ""))
    r = U.install()
    assert r["ok"], r
    assert ran and ran[0][-1].endswith("requirements-cli.txt") and "packaging" in ran[0][-1]
    assert (app / "requirements-cli.txt").read_text() == "pandas\nnewdep\n"


def test_cli_update_skips_pip_when_the_package_list_is_unchanged(monkeypatch, tmp_path):
    app = _installed(tmp_path)
    (app / "requirements-cli.txt").write_text("pandas\n")
    tgz = _release(tmp_path, "0.2.0", {"packaging/requirements-cli.txt": "pandas\n",
                                       "app/requirements.txt": "something else\n"})
    _update_to(monkeypatch, app, tgz, "0.2.0")
    monkeypatch.setattr(U.subprocess, "run", lambda *a, **k: (_ for _ in ()).throw(AssertionError("pip ran")))
    assert U.install()["ok"]


def test_failed_swap_puts_every_old_folder_back(monkeypatch, tmp_path):
    app = _installed(tmp_path)
    tgz = _release(tmp_path, "0.2.0", {"app/engines/new.py": "new"})
    _update_to(monkeypatch, app, tgz, "0.2.0")
    real = U.Path.rename

    def rename(self, target):
        if self.name == ".engines.new":
            raise PermissionError("in use")
        return real(self, target)
    monkeypatch.setattr(U.Path, "rename", rename)
    r = U.install()
    assert not r["ok"] and "Nothing was changed" in r["error"]
    assert '"0.1.0"' in (app / "meshpool_harness" / "__init__.py").read_text()     # swapped first, then put back
    assert (app / "engines" / "old.py").exists() and not (app / "engines" / "new.py").exists()
    assert sorted(p.name for p in app.iterdir()) == ["engines", "meshpool_harness"]  # no staged or backup folders


def test_restart_never_runs_code_from_the_users_folder():
    cmd = U._relaunch_cmd()
    assert "-P" in cmd or ("-c" in cmd and "del sys.path[0]" in cmd[cmd.index("-c") + 1])
