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
