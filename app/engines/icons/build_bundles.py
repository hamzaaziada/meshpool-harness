"""Build the compressed icon bundles in engines/icons/bundles (name -> minified SVG per style, plus tags) from npm
packages unpacked without running their scripts, one folder per package under SRC_DIR:

    npm pack --ignore-scripts @phosphor-icons/core@2.1.1 && tar -xzf phosphor-icons-core-2.1.1.tgz   # and so on
    python3 -I engines/icons/build_bundles.py SRC_DIR engines/icons/bundles

Folders read: _phosphor-icons_core_2.1.1, iconoir_7.12.2, heroicons_2.2.0, bootstrap-icons_1.13.2,
_material-symbols_svg-400_0.49.0, tabler_3.51.0, carbon_11.92.0 (each holding the unpacked "package" folder).

Inputs are untrusted: parsed with defusedxml, anything active (script, foreignObject, event handlers, external
references, images) is rejected, colours are mapped to currentColor and width/height/class/aria/data attributes are
dropped so each icon scales by its viewBox.
"""
import gzip
import json
import re
import sys
from pathlib import Path
from xml.etree import ElementTree as ET

import defusedxml.ElementTree as DET

SVG_NS = "http://www.w3.org/2000/svg"
ET.register_namespace("", SVG_NS)
SRC = OUT = Path(".")

ALLOWED = {"svg", "path", "g", "circle", "ellipse", "rect", "line", "polyline", "polygon", "defs", "clipPath",
           "mask", "title"}
DROP_ATTR = {"width", "height", "class", "aria-hidden", "role", "focusable", "data-slot"}
COLOR_ATTR = {"fill", "stroke", "color", "stop-color"}

# company and product logos (trademarks), left out like Tabler's Brand category
BRANDS = {
    "iconoir": {"adobe-after-effects", "adobe-illustrator", "adobe-indesign", "adobe-lightroom", "adobe-photoshop",
                "adobe-xd", "airplay", "app-store", "apple", "apple-imac-2021", "apple-imac-2021-side", "apple-mac",
                "apple-shortcuts", "apple-swift", "apple-wallet", "asana", "behance", "behance-tag", "bitbucket",
                "chromecast", "chromecast-active", "codepen", "creative-commons", "css3", "discord", "dribbble",
                "facebook", "facebook-tag", "facetime", "figma", "finder", "github", "github-circle", "gitlab-full",
                "google", "google-circle", "google-docs", "google-drive", "google-drive-check", "google-drive-sync",
                "google-drive-warning", "google-home", "google-one", "html5", "iconoir", "instagram", "linkedin",
                "linux", "mastercard-card", "mastodon", "medium", "nintendo-switch", "npm", "npm-square", "open-vpn",
                "pacman", "paypal", "peerlist", "pinterest", "playstation-gamepad", "pokeball", "polar-sh",
                "rubik-cube", "safari", "snapchat", "spotify", "stackoverflow", "telegram", "telegram-circle",
                "threads", "tiktok", "trello", "twitter", "unity", "unity-5", "vue-js", "whatsapp", "windows", "x",
                "xbox-a", "xbox-b", "xbox-x", "xbox-y", "yelp", "youtube"},
    "bootstrap": {"alexa", "alipay", "amazon", "amd", "android", "android2", "anthropic", "apple", "apple-music",
                  "behance", "bing", "bluesky", "bootstrap", "bootstrap-fill", "bootstrap-reboot", "browser-chrome",
                  "browser-edge", "browser-firefox", "browser-safari", "cc-circle", "cc-circle-fill", "cc-square",
                  "cc-square-fill", "claude", "css", "discord", "dribbble", "dropbox", "facebook", "git", "github",
                  "gitlab", "google", "google-play", "instagram", "javascript", "line", "linkedin", "markdown",
                  "markdown-fill", "mastodon", "medium", "messenger", "meta", "microsoft", "microsoft-teams",
                  "nintendo-switch", "nvidia", "openai", "opencollective", "paypal", "perplexity", "pinterest",
                  "playstation", "quora", "reddit", "signal", "sina-weibo", "skype", "slack", "snapchat",
                  "sourceforge", "spotify", "stack-overflow", "steam", "strava", "stripe", "substack", "telegram",
                  "tencent-qq", "threads", "threads-fill", "tiktok", "trello", "tux", "twitch", "twitter",
                  "twitter-x", "typescript", "ubuntu", "unity", "vimeo", "wechat", "whatsapp", "wikipedia",
                  "windows", "wordpress", "xbox", "yelp", "youtube"},
    "material": {"android", "flutter", "flutter-dash", "ios", "stadia-controller", "jamboard-kiosk",
                 "chromecast-2", "chromecast-device"},
}
BRAND_PREFIX = {"material": ("nest-", "mfg-nest-", "google-")}


def is_brand(fam, name):
    return name in BRANDS.get(fam, ()) or name.startswith(BRAND_PREFIX.get(fam, ("\0",)))


def _color(v):
    v = v.strip()
    if v in ("none", "currentColor", "inherit", "transparent") or v.startswith("url("):
        return v
    return "currentColor"


def normalise(path: Path, fill_root: bool) -> str:
    raw = path.read_bytes()
    if len(raw) > 200_000:
        raise ValueError("too large")
    root = DET.fromstring(raw, forbid_dtd=True)
    in_mask = {id(e) for m in root.iter() if m.tag.split("}")[-1] == "mask" for e in m.iter()}
    for el in root.iter():
        tag = el.tag.split("}")[-1]
        if tag not in ALLOWED:
            raise ValueError(f"element {tag}")
        for k in list(el.attrib):
            lk = k.split("}")[-1]
            if lk.startswith("on") or "href" in lk:
                raise ValueError(f"attribute {k}")
            if (lk in DROP_ATTR and (el is root or lk not in ("width", "height"))) or lk.startswith("data-") \
                    or (el is root and lk in ("xmlns", "version")):
                del el.attrib[k]
            elif lk in COLOR_ATTR and id(el) not in in_mask:
                el.attrib[k] = _color(el.attrib[k])
            elif lk == "style":
                st = re.sub(r"(fill|stroke|color)\s*:\s*([^;]+)", lambda m: f"{m.group(1)}:{_color(m.group(2))}",
                            el.attrib[k])
                if "url(" in st and "#" not in st:
                    raise ValueError("external url in style")
                el.attrib[k] = st
        if el.text and not el.text.strip():
            el.text = None
        if el.tail and not el.tail.strip():
            el.tail = None
        if tag == "title":
            el.text = None
    dead = [e for e in root.iter() if e.tag.split("}")[-1] == "title"
            or (e is not root and e.get("fill") == "none" and e.get("stroke") == "none")]
    for t in dead:
        for parent in root.iter():
            if t in list(parent):
                parent.remove(t)
    if "viewBox" not in root.attrib:
        raise ValueError("no viewBox")
    if fill_root and "fill" not in root.attrib:
        root.attrib["fill"] = "currentColor"
    s = ET.tostring(root, encoding="unicode")
    s = re.sub(r">\s+<", "><", s).strip()
    return s


def collect(fam, variants):
    """variants: {variant: (dir, suffix_to_strip, fill_root, name_filter)} -> {variant: {name: svg}}"""
    out, rejected, brands = {}, [], set()
    for var, (d, suffix, fill_root, keep) in variants.items():
        icons = {}
        for p in sorted(Path(d).glob("*.svg")):
            stem = p.stem
            if suffix and stem.endswith(suffix):
                stem = stem[: -len(suffix)]
            if keep and not keep(p.stem):
                continue
            name = stem.lower() if fam == "carbon" else stem.replace("_", "-").lower()
            if keep is not None and var.endswith("fill") and fam == "bootstrap":
                name = re.sub(r"-fill$", "", name)
            if is_brand(fam, name) or is_brand(fam, p.stem):
                brands.add(name)
                continue
            try:
                icons[name] = normalise(p, fill_root)
            except Exception as e:  # noqa: BLE001
                rejected.append(f"{var}/{p.name}: {e}")
        out[var] = icons
    return out, rejected, sorted(brands)


def phosphor_tags(mjs: Path):
    s = mjs.read_text(encoding="utf-8")
    cats = dict(re.findall(r'i\.([A-Z_]+) = "([^"]+)"', s.split("const n = [")[0]))
    tags = {}
    for m in re.finditer(r'\{\s*name: "([^"]+)",\s*pascal_name: "[^"]*",\s*categories: \[([^\]]*)\],'
                         r'\s*figma_category: [^,]+,\s*tags: \[([^\]]*)\]', s):
        name, c, t = m.group(1), m.group(2), m.group(3)
        cl = [cats.get(x.strip().split(".")[-1], "") for x in c.split(",") if x.strip()]
        tl = [x for x in re.findall(r'"([^"]*)"', t) if not x.startswith("*")]
        tags[name] = {"c": ", ".join(x for x in cl if x), "t": tl}
    return tags


def write(fam, meta, variants, tags):
    OUT.mkdir(parents=True, exist_ok=True)
    data = {"family": fam, **meta, "tags": tags, "variants": variants}
    blob = json.dumps(data, separators=(",", ":"), sort_keys=True, ensure_ascii=False).encode()
    p = OUT / f"{fam}.json.gz"
    with gzip.GzipFile(p, "wb", compresslevel=9, mtime=0) as f:
        f.write(blob)
    print(fam, {v: len(i) for v, i in variants.items()}, f"{p.stat().st_size / 1e6:.2f} MB")


def main():
    report = {}
    # Phosphor: categories and tags from the package's own metadata; the Brand category is left out
    ph = SRC / "_phosphor-icons_core_2.1.1" / "package"
    tags = phosphor_tags(ph / "dist" / "index.mjs")
    brand = {n for n, t in tags.items() if "brands" in t["c"]}
    BRANDS["phosphor"] = brand
    vs = {w: (ph / "assets" / w, "" if w == "regular" else f"-{w}", True, None)
          for w in ("regular", "light", "thin", "bold", "fill", "duotone")}
    v, rej, br = collect("phosphor", vs)
    write("phosphor", {"version": "2.1.1"}, v, {k: t for k, t in tags.items() if k not in brand})
    report["phosphor"] = (rej, br)

    io = SRC / "iconoir_7.12.2" / "package" / "icons"
    v, rej, br = collect("iconoir", {"regular": (io / "regular", "", False, None),
                                     "solid": (io / "solid", "", True, None)})
    write("iconoir", {"version": "7.12.2"}, v, {})
    report["iconoir"] = (rej, br)

    he = SRC / "heroicons_2.2.0" / "package" / "24"
    v, rej, br = collect("heroicons", {"outline": (he / "outline", "", False, None),
                                       "solid": (he / "solid", "", True, None)})
    write("heroicons", {"version": "2.2.0"}, v, {})
    report["heroicons"] = (rej, br)

    bs = SRC / "bootstrap-icons_1.13.2" / "package" / "icons"
    v, rej, br = collect("bootstrap", {"regular": (bs, "", True, lambda s: not s.endswith("-fill")),
                                       "fill": (bs, "", True, lambda s: s.endswith("-fill"))})
    write("bootstrap", {"version": "1.13.2"}, v, {})
    report["bootstrap"] = (rej, br)

    ms = SRC / "_material-symbols_svg-400_0.49.0" / "package"
    vs = {}
    for style in ("outlined", "rounded", "sharp"):
        vs[style] = (ms / style, "", True, lambda s: not s.endswith("-fill"))
        vs[style + "-fill"] = (ms / style, "-fill", True, lambda s: s.endswith("-fill"))
    v, rej, br = collect("material", vs)
    write("material", {"version": "0.49.0"}, v, {})
    report["material"] = (rej, br)

    tb = SRC / "tabler_3.51.0" / "package"
    meta = json.loads((tb / "icons.json").read_text(encoding="utf-8"))
    BRANDS["tabler"] = {n for n, m in meta.items() if m.get("category") == "Brand"}
    v, rej, br = collect("tabler", {"filled": (tb / "icons" / "filled", "", True, None)})
    write("tabler-filled", {"version": "3.51.0"}, v, {n: {"c": meta[n].get("category", ""), "t": meta[n].get("tags", [])}
                                                 for n in v["filled"] if n in meta})
    report["tabler"] = (rej, br)

    cb = SRC / "carbon_11.92.0" / "package"
    meta = json.loads((cb / "metadata.json").read_text(encoding="utf-8"))
    cat = {}
    brand = set()
    for c in meta["categories"]:
        for sc in c["subcategories"]:
            for m in sc["members"]:
                cat.setdefault(m.lower(), f"{c['name']}, {sc['name']}")
                if c["name"] == "Brand":
                    brand.add(m.lower())
    BRANDS["carbon"] = brand
    BRAND_PREFIX["carbon"] = ("logo--", "ibm", "watson", "red-hat", "carbon-for-ibm", "dfsort-ibm", "db2-",
                              "vlan--ibm")
    tags = {}
    for i in meta["icons"]:
        n = i["name"].lower()
        tags[n] = {"c": cat.get(n, ""), "t": [i.get("friendlyName", "")] + list(i.get("aliases") or [])}
    v, rej, br = collect("carbon", {"regular": (cb / "svg" / "32", "", True, None)})
    write("carbon", {"version": "11.92.0"}, v, {n: tags[n] for n in v["regular"] if n in tags})
    report["carbon"] = (rej, br)

    for fam, (rej, br) in report.items():
        print(f"{fam}: rejected {len(rej)} {rej[:5]}; brands left out {len(br)}")
    print(json.dumps({f: {"rejected": r, "brands_left_out": b} for f, (r, b) in report.items()}, indent=1))


if __name__ == "__main__":
    SRC, OUT = Path(sys.argv[1]), Path(sys.argv[2])
    main()
