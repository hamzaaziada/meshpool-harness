"""First run in the terminal: the same three steps as the app (key, where to work, bring your setup), then a short
tour, then the session starts.

  meshpool            runs this when there is no API key yet
  meshpool setup      runs it again any time
  meshpool login      only the key step
"""
from __future__ import annotations

import getpass
import os
import sys
from pathlib import Path

from meshpool_harness import config as C
from meshpool_harness.agent import brand as B

KEY_URL = "https://meshpool.ai"
STEPS = 3


def _p(s: str = "") -> None:
    print(s, flush=True)


def _ask(st, prompt: str, default: str = "") -> str | None:
    try:
        v = input(B.prompt(f"  {st.blue('›')} {prompt}" + (st.dim(f" [{default}]") if default else "") + " ")).strip()
    except (EOFError, KeyboardInterrupt):
        _p()
        return None
    return v or default


def _yes(st, prompt: str, default: bool = True) -> bool | None:
    v = _ask(st, prompt + st.dim(" (Y/n)" if default else " (y/N)"))
    if v is None:
        return None
    return default if not v else v.lower().startswith("y")


def _choose(st, options: list[tuple[str, str]], default: int = 1) -> int | None:
    """Numbered options (label, detail). Returns the 1-based choice."""
    for i, (label, detail) in enumerate(options, 1):
        num = st.blue(f"{i}") if i == default else st.dim(f"{i}")
        _p(f"    {num}  {st.bold(label)}" + (f"  {st.dim(detail)}" if detail else ""))
    _p()
    while True:
        v = _ask(st, "Choose", str(default))
        if v is None:
            return None
        if v.isdigit() and 1 <= int(v) <= len(options):
            return int(v)
        _p(st.amber(f"    Type a number from 1 to {len(options)}."))


def _step(st, n: int, title: str, lead: str = "") -> None:
    dots = " ".join(st.blue("●") if i <= n else st.dim("○") for i in range(1, STEPS + 1))
    _p()
    _p(f"  {dots}  {st.dim(f'Step {n} of {STEPS}')}")
    _p(f"  {st.bold(title)}")
    if lead:
        _p(f"  {st.dim(lead)}")
    _p()


def _short(p: Path) -> str:
    try:
        return "~/" + str(p.relative_to(Path.home())) if p != Path.home() else "~"
    except ValueError:
        return str(p)


# ---------- step 1: the key ---------------------------------------------------------------------------------------

KEY_ENV = ("MESHPOOL_BASE_URL", "MH_MODEL", "MESHPOOL_API_KEY")


def key_step(st, header: bool = False, n: int | None = None, custom: bool = False) -> bool:
    """Ask for the key, check it with one tiny model call, save it only if it works. With custom, ask for another
    provider's OpenAI-compatible address first. The endpoint, model and key in this process change only when a key is
    saved: a cancel (Ctrl+C, end of input) or failed keys put the old ones back, so a running session never sends the
    old key to the new address."""
    before = {k: os.environ.get(k) for k in KEY_ENV}
    ok = False
    try:
        ok = _key_step(st, header, n, custom)
        return ok
    finally:
        if not ok:
            for k, v in before.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v


def _check_model(custom: bool) -> str:
    """A model for the key check that the endpoint just entered serves: never a chatgpt:/anthropic: model, which would
    test that sign-in or key instead of the pasted one."""
    from meshpool_harness import providers as PV
    m = (os.environ.get("MH_MODEL") or "").strip()
    head, sep, bare = m.partition(":")
    if sep and head in PV.PREFIXED:
        return bare if custom and bare else "deepseek-v4.1-flash"
    return m or "deepseek-v4.1-flash"


def _key_step(st, header: bool, n: int | None, custom: bool) -> bool:
    from meshpool_harness.cli import ping
    if header:
        _p()
        for row in B.banner(st.depth):
            _p(row)
    title = "Connect your provider" if custom else "Connect your Meshpool account"
    where = "Your key stays on this computer, in " + _short(C.config_path())
    if n:
        _step(st, n, title, where)
    elif header:
        _p()
        _p(f"  {st.bold(title)}")
        _p(f"  {st.dim(where)}")
        _p()
    else:
        _p(f"  {st.dim(where)}")
        _p()
    old_base = os.environ.get("MESHPOOL_BASE_URL")
    if custom:
        _p(st.dim("  Any OpenAI-compatible provider: OpenAI, OpenRouter, Together, Groq, a local server, and others."))
        _p()
        base = _ask(st, "Provider address, ending in /v1", old_base if old_base and "meshpool" not in old_base
                    else "https://api.openai.com/v1")
        if not base:
            return False
        os.environ["MESHPOOL_BASE_URL"] = base.rstrip("/")
        model = _ask(st, "Model id", os.environ.get("MH_MODEL") if not (os.environ.get("MH_MODEL") or "").startswith(
            "deepseek-v4") else "gpt-4.1-mini")
        if not model:
            return False
        os.environ["MH_MODEL"] = model
        _p()
    else:
        _p(f"  No key yet? Get one at {st.blue(KEY_URL)} and run every model 20% cheaper.")
        _p(st.dim("  Press Enter with nothing typed to open it."))
        _p()
    reset = {}
    saved_base = (C.read().get("MESHPOOL_BASE_URL") or "").rstrip("/")
    if not custom and saved_base not in ("", "https://llm.meshpool.ai/v1"):
        # Back to the Meshpool key from another provider: its address and model do not apply any more.
        reset = {"MESHPOOL_BASE_URL": "", "MH_MODEL": ""}
        os.environ["MESHPOOL_BASE_URL"] = "https://llm.meshpool.ai/v1"
        os.environ.pop("MH_MODEL", None)
    for _ in range(5):
        try:
            key = getpass.getpass(f"  {st.blue('›')} Paste your API key {st.dim('(hidden as you type)')} ").strip()
        except (EOFError, KeyboardInterrupt):
            _p()
            return False
        if not key and custom:
            continue
        if not key:
            try:
                import webbrowser
                webbrowser.open(KEY_URL)
                _p(st.dim(f"    Opened {KEY_URL}. Copy your key there, then paste it here."))
            except Exception:  # noqa: BLE001
                _p(st.dim(f"    Open {KEY_URL} in your browser, copy your key, then paste it here."))
            continue
        os.environ["MESHPOOL_API_KEY"] = key
        _p(st.dim("    Checking your key..."))
        r = ping(_check_model(custom))
        save = {"MESHPOOL_API_KEY": key, **reset}
        if custom:
            save.update(MESHPOOL_BASE_URL=os.environ["MESHPOOL_BASE_URL"], MH_MODEL=os.environ["MH_MODEL"])
        if r["ok"]:
            C.write(save)
            _use("custom" if custom else "meshpool")
            _p(f"    {st.green('✓')} Connected. {r['model']} answered in {r['seconds']} s.")
            return True
        msg = r["detail"]
        if "rejected the API key" in msg or "401" in msg or "403" in msg:
            _p(st.red("    That key did not work. Check you copied all of it, then paste it again."))
            continue
        # The key may be fine and the network is the problem: keep it, say what happened.
        C.write(save)
        _use("custom" if custom else "meshpool")
        _p(st.amber(f"    Saved, but Meshpool could not reach the model: {msg[:200]}"))
        _p(st.dim("    Check your internet connection. `meshpool check --live` tests it again."))
        return True
    return False                                  # key_step puts the old endpoint, model and key back


def _use(which: str) -> None:
    from meshpool_harness import harnesses as HR
    HR.use_provider(which)


SUBS = {"claude": ("Claude", "Claude Pro or Max", "@anthropic-ai/claude-code@latest"),
        "chatgpt": ("ChatGPT", "ChatGPT Plus, Pro or Business", "@openai/codex@latest")}


def sub_step(st, which: str) -> bool:
    """Sign in to a Claude or ChatGPT subscription with the official Claude Code or Codex sign-in, right here."""
    import shutil
    import subprocess
    from meshpool_harness import harnesses as HR
    name, plans, pkg = SUBS[which]
    login = HR.LOGINS[which]
    if HR.signed_in(login):
        _p(f"    {st.green('✓')} You are already signed in to {name} on this computer.")
        _use(which)
        return True
    npx = shutil.which("npx")
    if not npx:
        _p(st.amber(f"    Signing in to {name} needs Node.js, which is not installed."))
        _p(st.dim("    Install it from https://nodejs.org (the LTS version), then run `meshpool setup` again."))
        _p(st.dim("    The Meshpool desktop app includes it."))
        return False
    _p(f"  Usage comes out of your {plans} plan. No API key needed.")
    if which == "claude":
        _p(st.dim("  A Claude subscription runs Claude Code, Anthropic's own agent. Meshpool never uses its sign-in"))
        _p(st.dim("  for anything else."))
    _p()
    if which == "chatgpt":
        _p(st.dim("    Your browser opens for the ChatGPT sign-in. Come back here when it says you are signed in."))
        cmd = [npx, "-y", pkg, "login"]
    else:
        _p(st.dim("    Claude Code opens here. Sign in with your Claude account (type /login if it does not ask),"))
        _p(st.dim("    then type /exit to come back to Meshpool."))
        cmd = [npx, "-y", pkg]
    if _ask(st, "Press Enter to sign in") is None:
        return False
    try:
        subprocess.run(cmd)
    except (OSError, KeyboardInterrupt):
        pass
    if HR.signed_in(login):
        _p(f"    {st.green('✓')} Signed in to {name}.")
        _use(which)
        return True
    _p(st.red(f"    Not signed in to {name} yet."))
    return False


def _agent_note(st) -> None:
    """Say which agent the provider gives: the default agent is chosen from the provider."""
    from meshpool_harness import harnesses as HR
    try:
        h = HR.get(HR.default_for("code"))
    except (KeyError, ValueError):
        return
    _p(f"    {st.green('✓')} Your agent: {h['name']}" + (f", on {h['runs_on']}." if h.get("runs_on") else "."))
    if HR.provider() == "claude":
        _p(st.dim("    Anthropic's terms let a Claude subscription run only Claude Code, so Meshpool Code, Work, Pi"))
        _p(st.dim("    and Hermes need an API key or a ChatGPT sign-in. Add one any time with `meshpool setup`."))


def provider_step(st, n: int = 1) -> bool:
    """Step 1: what Meshpool runs on. The Meshpool key is the default; any other key or a subscription also works."""
    _step(st, n, "Choose how Meshpool runs", "You can change this any time with `meshpool setup`.")
    from meshpool_harness import harnesses as HR
    opts = [("meshpool", "Meshpool API key", "recommended: every model on one key, 20% cheaper"),
            ("custom", "Another API key", "OpenAI, Anthropic, OpenRouter or any OpenAI-compatible provider"),
            ("chatgpt", "ChatGPT subscription", "your ChatGPT Plus, Pro or Business plan, for Meshpool's agents, "
                                                "Pi, Hermes and Codex"),
            ("claude", "Claude subscription, runs Claude Code", "your Claude Pro or Max plan; only Claude Code "
                                                                "can use it")]
    cur = HR.provider()
    d = next((i for i, o in enumerate(opts, 1) if o[0] == cur), 1)
    while True:
        c = _choose(st, [(o[1], o[2]) for o in opts], d)
        if c is None:
            return False
        which = opts[c - 1][0]
        _p()
        if which in SUBS:
            ok = sub_step(st, which)
        else:
            has = bool(os.environ.get("MESHPOOL_API_KEY")) and which == cur
            if has:
                _p(f"    {st.green('✓')} Your key is saved (ends {os.environ['MESHPOOL_API_KEY'][-4:]}).")
                keep = _yes(st, "Keep it?")
                if keep is None:
                    return False
                if keep:
                    _use(which)
                    _agent_note(st)
                    return True
            ok = key_step(st, custom=which == "custom")
        if ok:
            _agent_note(st)
            return True
        _p()
        _p(st.dim("  Choose again, or press Ctrl+C to leave."))
        _p()


# ---------- step 2: where it works and what it may do -------------------------------------------------------------

def _where_step(st, start: Path) -> Path | None:
    _step(st, 2, "Choose where Meshpool works",
          "It reads and changes files only in the folder you start it in, and its subfolders.")
    projects = Path.home() / "Meshpool Projects"
    broad = start in (Path.home(), Path(start.anchor))
    opts = [("Meshpool Projects", _short(projects) + ", a folder for your work")]
    if not broad:
        opts.insert(0, ("This folder", _short(start)))
    opts.append(("Another folder", "type its path"))
    c = _choose(st, opts, 1)
    if c is None:
        return None
    label = opts[c - 1][0]
    if label == "This folder":
        folder = start
    elif label == "Meshpool Projects":
        folder = projects
    else:
        while True:
            v = _ask(st, "Folder path")
            if v is None:
                return None
            folder = Path(os.path.expandvars(v.strip().strip("'\""))).expanduser()
            if folder.is_dir():
                break
            if v and _yes(st, f"{_short(folder)} does not exist. Create it?"):
                break
    folder.mkdir(parents=True, exist_ok=True)
    folder = folder.resolve()
    _p(f"    {st.green('✓')} Working in {_short(folder)}")

    _p()
    _p(f"  {st.bold('What can it do without asking?')}")
    _p()
    modes = [("ask", "Ask me first", "before it changes a file or runs a command (recommended)"),
             ("edits", "Edit files freely", "ask only before running commands"),
             ("auto", "Full access", "never ask")]
    cur = os.environ.get("MH_AGENT_PERMISSIONS", "ask")
    d = next((i for i, m in enumerate(modes, 1) if m[0] == cur), 1)
    c = _choose(st, [(m[1], m[2]) for m in modes], d)
    if c is None:
        return None
    C.write({"MH_AGENT_PERMISSIONS": modes[c - 1][0]})
    os.environ["MH_AGENT_PERMISSIONS"] = modes[c - 1][0]
    _p(f"    {st.green('✓')} {modes[c - 1][1]}. Change it any time with /auto.")
    return folder


# ---------- step 3: bring your setup ------------------------------------------------------------------------------

def _import_step(st) -> bool:
    _step(st, 3, "Bring your setup", "Your skills, tools and what your current assistant knows about you.")
    try:
        from meshpool_harness import importer
        found = importer.scan()
    except Exception as e:  # noqa: BLE001
        _p(st.dim(f"    Skipped: could not look for them ({e})."))
        return True
    rows = [r for r in found["skills"] + found["mcp"] if not r.get("exists") and r.get("suggested", True)]
    if not rows:
        _p(st.dim("    Nothing new found on this computer. You can add skills and MCP servers later (meshpool ui)."))
        return True
    skills = [r for r in rows if r["kind"] == "skill"]
    mcp = [r for r in rows if r["kind"] != "skill"]
    sources = sorted({r["source"] for r in rows})
    parts = ([f"{len(skills)} skill{'s' * (len(skills) != 1)}"] if skills else []) + \
            ([f"{len(mcp)} MCP server{'s' * (len(mcp) != 1)}"] if mcp else [])
    _p(f"  Found {' and '.join(parts)} in {', '.join(sources)}:")
    names = [r["name"] for r in rows]
    _p(st.dim("    " + ", ".join(names[:12]) + (f" and {len(names) - 12} more" if len(names) > 12 else "")))
    _p()
    yes = _yes(st, "Import them?")
    if yes is None:
        return False
    if yes:
        done = importer.run([r["key"] for r in rows])
        _p(f"    {st.green('✓')} Imported {len(done['skills'])} skills and {len(done['mcp'])} MCP servers.")
        for err in done["errors"][:5]:
            _p(st.amber("    " + err))
    else:
        _p(st.dim("    Skipped. You can import them later in the app (meshpool ui)."))
    return True


def _copy(text: str) -> bool:
    import shutil
    import subprocess
    for cmd in (["pbcopy"], ["clip"], ["wl-copy"], ["xclip", "-selection", "clipboard"], ["xsel", "-ib"]):
        if shutil.which(cmd[0]):
            try:
                subprocess.run(cmd, input=text.encode("utf-16le" if cmd[0] == "clip" else "utf-8"), timeout=5,
                               check=True)
                return True
            except Exception:  # noqa: BLE001
                continue
    return False


def _paste(st) -> str | None:
    _p(st.dim("  Paste the answer below. When it is all in, type a single . on its own line and press Enter."))
    _p()
    lines = []
    while True:
        try:
            line = input()
        except EOFError:
            break
        except KeyboardInterrupt:
            _p()
            return None
        if line.strip() == ".":
            break
        lines.append(line)
    return "\n".join(lines).strip()


def context_step(st, header: bool = True) -> bool:
    """Bring your chats and context: a ChatGPT or Claude export (every chat becomes a thread; projects become skills;
    lasting facts become memory), the Claude Code and Codex sessions on this computer, or an assistant's answer to
    a prompt (instructions and memory)."""
    from meshpool_harness import chat_import as CI
    from meshpool_harness import importer
    if header:
        _p()
        _p(f"  {st.bold('Bring your chats and context')}")
        _p(f"  {st.dim('Old chats come over as chats you can search and continue. Nothing leaves this computer.')}")
        _p()
    local = {k: v for k, v in CI.scan_local().items() if v["found"] > v["imported"]}
    opts = [("zip", "Import a ChatGPT or Claude export (.zip)", "your chats, projects and what it knows about you")]
    if local:
        opts.append(("local", "Import sessions from this computer",
                     ", ".join(f"{v['name']}: {v['found'] - v['imported']} new" for v in local.values())))
    opts += [("paste", "Paste from ChatGPT, Claude or Gemini", "copy a prompt there, paste its answer here"),
             ("skip", "Skip", "do it later with /import or meshpool import")]
    c = _choose(st, [(o[1], o[2]) for o in opts], 1)
    if c is None:
        return False
    pick = opts[c - 1][0]
    if pick == "skip":
        return True
    if pick == "local":
        _p(st.dim("    Importing..."))
        r = CI.import_local(list(local))
        _p(f"    {st.green('✓')} Imported {r['imported']} sessions as chats"
           + (st.dim(f" ({r['skipped']} were already here)") if r["skipped"] else "") + ". They open in their project "
           "folders.")
        return True
    if pick == "paste":
        prompt = importer.context_prompt()
        copied = _copy(prompt)
        _p()
        _p("  " + (st.green("✓") + " Copied this prompt. " if copied else "Copy this prompt. ")
           + "Paste it into ChatGPT, Claude or Gemini, then copy the whole answer:")
        _p()
        for line in prompt.splitlines():
            _p(st.dim("    " + line))
        _p()
        text = _paste(st)
        if not text:
            _p(st.dim("    Nothing pasted. Run /import any time."))
            return True
        r = importer.import_context_text(text)
        _p(f"    {st.green('✓')} Imported {r['instructions']} instructions and {r['memory']} facts into memory."
           + (st.dim(f" Skipped {len(r['skipped'])} (secrets or system instructions).") if r["skipped"] else ""))
        return True
    _p(st.dim("  ChatGPT: Settings, Data controls, Export data. Claude: Settings, Privacy, Export data."))
    _p(st.dim("  Both email you a .zip. Keep it zipped."))
    while True:
        v = _ask(st, "Path to the .zip " + st.dim("(you can drag the file into this window)"))
        if not v:
            return v is not None
        path = Path(v.strip().strip("'\"").replace("\\ ", " ")).expanduser()
        if path.is_file():
            break
        _p(st.amber(f"    No file at {path}."))
    _p(st.dim("    Importing your export..."))
    try:
        r = CI.import_export(str(path))
    except Exception as e:  # noqa: BLE001
        _p(st.red(f"    Could not import it: {e}"))
        return True
    _p(f"    {st.green('✓')} {'Claude' if r['kind'] == 'claude' else 'ChatGPT'} export: {r['imported']} chats"
       + (f" ({r['skipped']} were already here)" if r["skipped"] else "")
       + f", {len(r['skills'])} project skills, {r['memory']} facts into memory, {r['instructions']} instructions.")
    _p(st.dim("    Find them in the app (meshpool ui), or ask the agent about an old chat."))
    return True


# ---------- the tour ----------------------------------------------------------------------------------------------

def tour(st) -> None:
    _p()
    _p(f"  {st.green('✓')} {st.bold('You are set up.')} Here is how it works:")
    _p()
    rows = [("Just type what you want", 'e.g. "summarize the PDFs here" or "fix the failing test"'),
            ("Enter", "sends it. End a line with \\ to keep typing"),
            ("Ctrl+C", "stops the current step. Twice quits"),
            ("/help", "all commands. /model switches models, /import brings your old chats"),
            ("meshpool", "starts it again later, in any folder"),
            ("meshpool ui", "the full app in your browser")]
    w = max(len(a) for a, _ in rows)
    for a, b in rows:
        _p(f"    {st.blue(a.ljust(w))}  {st.dim(b)}")
    _p()


def run(start: str | None = None, st=None) -> Path | None:
    """The whole walkthrough. Returns the folder to start the session in, or None if the user left."""
    from meshpool_harness.agent.tui import Style, _enable_ansi
    st = st or Style(_enable_ansi())
    if not sys.stdin.isatty():
        print(f"Meshpool setup needs a terminal. Run `meshpool setup` in Terminal, "
              f"or add MESHPOOL_API_KEY=... to {C.config_path()}", file=sys.stderr)
        return None
    C.load_into_env()
    _p()
    if os.environ.pop("MESHPOOL_FROM_INSTALLER", ""):     # the installer just showed the logo
        _p(f"  {st.bold('Welcome to Meshpool.')} {st.dim('Three quick steps and you can start.')}")
    else:
        for row in B.banner(st.depth, ["", st.bold("Welcome to Meshpool."),
                                       st.dim("Three quick steps and you can start.")]):
            _p(row)
    if not provider_step(st, 1):
        _p(st.red("  Setup stopped. Run `meshpool setup` to finish it."))
        return None
    folder = _where_step(st, Path(start or os.getcwd()).resolve())
    if folder is None or not _import_step(st) or not context_step(st):
        return None
    C.write({"MH_CLI_ONBOARDED": "1"})
    tour(st)
    return folder
