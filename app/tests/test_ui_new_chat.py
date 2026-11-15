"""The New chat button, clicked in a real browser against the real app server.

The other suites call newThread() from script, so they never saw that the button's click handler passed the click
event in as the chat's folder and threw before anything changed on screen. These tests click the button itself, from
the home screen and from an open chat, and check the folder, the screen and the page's errors.
"""
from __future__ import annotations

import json
import os
import re
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest

sync_api = pytest.importorskip("playwright.sync_api")

REPO = Path(__file__).resolve().parents[2]


def _port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait(url: str, secs: int = 60) -> str:
    end = time.time() + secs
    while time.time() < end:
        try:
            with urllib.request.urlopen(url, timeout=3) as r:
                return r.read().decode("utf-8", "replace")
        except Exception:  # noqa: BLE001
            time.sleep(0.5)
    pytest.fail(f"nothing answered at {url} within {secs} s")


@pytest.fixture(scope="module")
def app(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("newchat")
    home, mh, proj = tmp / "home", tmp / "mh", tmp / "proj"
    for d in (home, mh, proj):
        d.mkdir()
    (proj / "notes.txt").write_text("hello\n")
    mport, uport = _port(), _port()
    (mh / "meshpool.config").write_text(f"MESHPOOL_API_KEY=test-key-1234\nMESHPOOL_BASE_URL=http://127.0.0.1:{mport}/v1\n")
    env = {k: v for k, v in os.environ.items() if not re.match(r"(OPENAI_|CODEX_|GITHUB_|GH_)", k)}
    env.update(HOME=str(home), MESHPOOL_HOME=str(mh), PYTHONPATH=str(REPO / "app"), PYTHONUTF8="1", MH_CLARITY="0")
    env.pop("MESHPOOL_CONFIG", None)
    env["MH_SLOTS_DIR"] = str(tmp / "slots")        # never the real slot folder (conftest checks it)
    procs = [subprocess.Popen([sys.executable, str(REPO / "testkit" / "mock_llm.py"), str(mport), str(tmp / "mock.jsonl")],
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)]
    log = open(tmp / "ui.log", "w")
    procs.append(subprocess.Popen([sys.executable, "-m", "meshpool_harness", "ui", "--no-browser", "--port", str(uport)],
                                  cwd=str(REPO / "app"), env=env, stdout=log, stderr=subprocess.STDOUT))
    base = f"http://127.0.0.1:{uport}"
    try:
        tok = re.search(r'token: "([^"]+)"', _wait(base + "/")).group(1)
        req = urllib.request.Request(base + "/api/projects", data=json.dumps({"action": "add", "path": str(proj)}).encode(),
                                     headers={"X-Meshpool-Token": tok, "Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=30).read()
        yield {"base": base, "proj": str(proj), "log": tmp / "ui.log"}
    finally:
        for p in procs:
            p.kill()
        log.close()


@pytest.fixture()
def page(app):
    with sync_api.sync_playwright() as pw:
        exe = os.environ.get("MH_TEST_CHROMIUM")     # a Chromium of another Playwright version, for local runs
        try:
            b = pw.chromium.launch(**({"executable_path": exe} if exe else {}))
        except Exception as e:  # noqa: BLE001
            if os.environ.get("CI"):
                raise                                 # CI installs Chromium: a missing browser must not pass quietly
            pytest.skip(f"no Chromium for Playwright here: {e}")
        pg = b.new_page(viewport={"width": 1280, "height": 860})
        errors = []
        pg.on("pageerror", lambda e: errors.append(str(e)))
        pg.add_init_script("try { localStorage.setItem('mp_onbDone', '1'); } catch (e) {}")
        pg.goto(app["base"] + "/")
        pg.wait_for_selector("#newThread", timeout=30000)
        pg.wait_for_function("() => typeof S === 'object' && S.projects && S.projects.length > 0", timeout=30000)
        pg.errors = errors
        yield pg
        b.close()


def _state(pg):
    return pg.evaluate("""() => ({cur: S.cur ? S.cur.id : null, project: S.draft.project,
        projectType: typeof S.draft.project, hero: !!document.querySelector('#col .hero'),
        stored: localStorage.getItem('mp_project')})""")


def test_new_chat_from_the_home_screen_keeps_a_real_folder(page):
    page.click("#newThread")
    page.wait_for_timeout(300)
    st = _state(page)
    assert page.errors == [], page.errors
    assert st["projectType"] == "string" and st["project"], st
    assert st["hero"] and st["cur"] is None, st
    assert page.evaluate("document.activeElement && document.activeElement.id") == "input"


def test_new_chat_from_an_open_chat_starts_a_new_one_in_the_same_folder(page, app):
    page.evaluate("p => { S.draft.project = p; S.draft.mode = 'auto'; renderHero(); }", app["proj"])
    page.fill("#input", "Say hello")
    page.press("#input", "Enter")
    page.wait_for_function("() => S.cur && S.cur.view && S.cur.view.workdir", timeout=60000)
    first = page.evaluate("S.cur.id")
    workdir = page.evaluate("S.cur.view.workdir")
    try:
        page.wait_for_function("() => S.cur && S.cur.view && S.cur.view.state === 'idle'", timeout=90000)
    except Exception:  # noqa: BLE001
        pytest.fail(f"the first chat did not finish: {page.evaluate('JSON.stringify(S.cur && S.cur.view && S.cur.view.state)')}\n"
                    + app["log"].read_text(errors="replace")[-2000:])

    page.click("#newThread")
    page.wait_for_timeout(300)
    st = _state(page)
    assert page.errors == [], page.errors
    assert st["cur"] is None and st["hero"], st
    assert st["project"] == workdir, st
    assert page.text_content("#heroProj").strip() == Path(workdir).name

    page.fill("#input", "Say hello again")
    page.press("#input", "Enter")
    # the new chat's view starts as a placeholder; its folder arrives with the first poll of the server
    page.wait_for_function("id => S.cur && S.cur.id && S.cur.id !== id && S.cur.view && S.cur.view.workdir", arg=first,
                           timeout=60000)
    assert page.evaluate("S.cur.view.workdir") == workdir
    assert page.errors == [], page.errors


def test_a_folder_saved_as_a_click_event_by_an_earlier_version_is_ignored(app):
    """0.0.5 and earlier saved {"isTrusted":true} as the folder when New chat was clicked; the app must still work."""
    with sync_api.sync_playwright() as pw:
        exe = os.environ.get("MH_TEST_CHROMIUM")
        try:
            b = pw.chromium.launch(**({"executable_path": exe} if exe else {}))
        except Exception as e:  # noqa: BLE001
            if os.environ.get("CI"):
                raise
            pytest.skip(f"no Chromium for Playwright here: {e}")
        pg = b.new_page()
        errors = []
        pg.on("pageerror", lambda e: errors.append(str(e)))
        pg.add_init_script("""try { localStorage.setItem('mp_onbDone', '1');
            localStorage.setItem('mp_project', JSON.stringify({isTrusted: true})); } catch (e) {}""")
        pg.goto(app["base"] + "/")
        pg.wait_for_function("() => typeof S === 'object' && S.projects && S.projects.length > 0", timeout=30000)
        pg.wait_for_selector("#col .hero", timeout=30000)
        pg.click("#newThread")
        pg.wait_for_timeout(300)
        assert errors == [], errors
        assert pg.evaluate("typeof S.draft.project") == "string"
        assert pg.evaluate("S.draft.project") != ""
        b.close()
