"""Drive the harness browser tool on a local MiniWoB page by hand (no model calls).

  python3 mw_probe.py <miniwob html dir> <task> <seed> '<ctl cmd>' ['<ctl cmd>' ...]
Each ctl cmd is the argument list for engines.browser.ctl, e.g. 'click 3' or 'type 2 Boston'.
Prints the utterance, each output the model would see, its size, and the reward at the end."""
import functools, http.server, json, os, shlex, socket, subprocess, sys, threading
from pathlib import Path

APP = Path(__file__).resolve().parents[1] / "app"
SETUP = """() => { Math.seedrandom('%s'); core.EPISODE_MAX_TIME = 3600000; var o = core.endEpisode;
  core.endEpisode = function (r, t, w) { if (window.__mh === undefined) { window.__mh = {raw: r}; } return o(r, t, w); };
  core.startEpisodeReal(); return core.getUtterance(); }"""


def port():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close(); return p


def main(html, task, seed, cmds):
    hp = port()
    h = functools.partial(type("Q", (http.server.SimpleHTTPRequestHandler,), {"log_message": lambda *a: None}),
                          directory=html)
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", hp), h)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    os.environ.update(MH_BROWSER_PORT=str(port()), MH_BROWSER_DIFF=os.environ.get("MH_BROWSER_DIFF", "1"),
                      PYTHONPATH=str(APP))
    sys.path.insert(0, str(APP))
    from engines.browser import ctl
    ctl.main(["start"])
    try:
        ctl.call("goto", f"http://127.0.0.1:{hp}/miniwob/{task}.html")
        print("UTTERANCE:", ctl.call("_eval", SETUP % f"mh-{seed}")["value"])
        first = ctl.render(ctl.call("snapshot"))
        print(f"--- prefetch ({len(first)} chars)\n{first}")
        for c in cmds:
            r = subprocess.run([sys.executable, "-m", "engines.browser.ctl", *shlex.split(c)], cwd=APP,
                               capture_output=True, text=True, env=os.environ)
            out = (r.stdout + r.stderr).strip()
            print(f"--- $ {c}  ({len(out)} chars)\n{out}")
        print("REWARD:", ctl.call("_eval", "() => JSON.stringify(window.__mh || null)").get("value"))
    finally:
        ctl.main(["stop"])
        srv.shutdown()


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4:])
