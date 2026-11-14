"""Icon library: every bundled icon set, indexed for keyword and synonym search.

Sets (one family can have several styles; each style is its own set, so a deck can stay in one style)
  lucide     1,539 icons, ISC; outline 2px (engines/slides/icons, loose SVGs); tags from lucide-static 0.460.0
  tabler     4,855 outline icons, MIT (engines/icons/tabler, loose SVGs); @tabler/icons 3.51.0 without the Brand
             category (company logos are trademarks, not icons we should put on slides)
  bundled (engines/icons/bundles/<family>.json.gz: name -> minified SVG per style, plus the package's own tags):
  tabler-filled                                      1,019 filled Tabler icons, MIT
  phosphor, -light, -thin, -bold, -fill, -duotone    1,434 each, MIT, @phosphor-icons/core 2.1.1 (no Brands)
  iconoir, iconoir-solid                             1,298 outline 1.5px / 272 solid, MIT, iconoir 7.12.2
  heroicons, heroicons-solid                         324 each, MIT, heroicons 2.2.0 (24px outline 1.5px / solid)
  bootstrap, bootstrap-fill                          1,323 / 665, MIT, bootstrap-icons 1.13.2
  material, -fill, -rounded, -rounded-fill, -sharp, -sharp-fill
                                                     3,885 each, Apache-2.0, Material Symbols (weight 400) 0.49.0
  carbon                                             2,353, Apache-2.0, IBM Carbon @carbon/icons 11.92.0 (32px)
  Company and product logos are left out of every set.

Use one set per deck so the strokes and corner styles match: pass `set=` to search(), or let best_set() choose the
set that covers most of a deck's meanings. Without `set`, search() looks in each family's main style (`style=`
"outline", "filled" or "duotone" picks that style of each family instead).

    search("money")                  -> [{"name": "circle-dollar-sign", "set": "lucide", "score": ..., "path": ...,
                                          "license": "ISC", "style": "outline", ...}, ...]
    search("money", set="phosphor")
    search("money", style="filled")
    path("coin", "tabler")           -> Path to the SVG (bundled sets are written to a cache folder on first use)
    path("tabler:coin")              -> same, with the set in the name
    svg("phosphor-fill:bank")        -> the SVG text (currentColor, so it recolours)
"""
from __future__ import annotations

import ast
import gzip
import json
import os
import re
import tempfile
from functools import lru_cache
from pathlib import Path

HERE = Path(__file__).resolve().parent
ENGINES = HERE.parent
BUNDLES = HERE / "bundles"
CACHE = Path(os.environ.get("MESHPOOL_ICON_CACHE") or Path(tempfile.gettempdir()) / "meshpool_icons")

_LUCIDE = {"family": "lucide", "license": "ISC", "license_file": ENGINES / "slides" / "icons" / "LICENSE-lucide.txt",
           "version": "0.460.0", "source": "https://lucide.dev (npm lucide-static)"}
_TABLER = {"family": "tabler", "license": "MIT", "license_file": HERE / "tabler" / "LICENSE-tabler.txt",
           "version": "3.51.0", "source": "https://tabler.io/icons (npm @tabler/icons)"}
_PHOSPHOR = {"family": "phosphor", "license": "MIT", "license_file": BUNDLES / "LICENSE-phosphor.txt",
             "version": "2.1.1", "source": "https://phosphoricons.com (npm @phosphor-icons/core)",
             "bundle": BUNDLES / "phosphor.json.gz"}
_ICONOIR = {"family": "iconoir", "license": "MIT", "license_file": BUNDLES / "LICENSE-iconoir.txt",
            "version": "7.12.2", "source": "https://iconoir.com (npm iconoir)", "bundle": BUNDLES / "iconoir.json.gz"}
_HERO = {"family": "heroicons", "license": "MIT", "license_file": BUNDLES / "LICENSE-heroicons.txt",
         "version": "2.2.0", "source": "https://heroicons.com (npm heroicons)", "bundle": BUNDLES / "heroicons.json.gz"}
_BOOT = {"family": "bootstrap", "license": "MIT", "license_file": BUNDLES / "LICENSE-bootstrap-icons.txt",
         "version": "1.13.2", "source": "https://icons.getbootstrap.com (npm bootstrap-icons)",
         "bundle": BUNDLES / "bootstrap.json.gz"}
_MAT = {"family": "material", "license": "Apache-2.0", "license_file": BUNDLES / "LICENSE-material-symbols.txt",
        "version": "0.49.0", "source": "https://fonts.google.com/icons (npm @material-symbols/svg-400)",
        "bundle": BUNDLES / "material.json.gz"}
_CARBON = {"family": "carbon", "license": "Apache-2.0", "license_file": BUNDLES / "LICENSE-carbon.txt",
           "version": "11.92.0", "source": "https://carbondesignsystem.com (npm @carbon/icons)",
           "bundle": BUNDLES / "carbon.json.gz"}

# kind: the style filter ("outline", "filled", "duotone"); style: what it looks like; primary: the family's main
# style, searched when no set is named. Order matters: earlier sets win ties and are tried first by path().
SETS = {
    "lucide": {**_LUCIDE, "dir": ENGINES / "slides" / "icons", "tags": HERE / "tags-lucide.json",
               "style": "outline, 2px stroke, rounded", "kind": "outline", "primary": True},
    "tabler": {**_TABLER, "dir": HERE / "tabler", "tags": HERE / "tags-tabler.json",
               "style": "outline, 2px stroke, rounded", "kind": "outline", "primary": True},
    "tabler-filled": {**_TABLER, "bundle": BUNDLES / "tabler-filled.json.gz", "variant": "filled",
                      "style": "filled, 24px grid", "kind": "filled"},
    "phosphor": {**_PHOSPHOR, "variant": "regular", "style": "outline look, regular weight, rounded",
                 "kind": "outline", "primary": True},
    "phosphor-light": {**_PHOSPHOR, "variant": "light", "style": "outline look, light weight, rounded",
                       "kind": "outline"},
    "phosphor-thin": {**_PHOSPHOR, "variant": "thin", "style": "outline look, thin weight, rounded", "kind": "outline"},
    "phosphor-bold": {**_PHOSPHOR, "variant": "bold", "style": "outline look, bold weight, rounded", "kind": "outline"},
    "phosphor-fill": {**_PHOSPHOR, "variant": "fill", "style": "filled", "kind": "filled"},
    "phosphor-duotone": {**_PHOSPHOR, "variant": "duotone", "style": "duotone: outline over a 20% tint",
                         "kind": "duotone"},
    "iconoir": {**_ICONOIR, "variant": "regular", "style": "outline, 1.5px stroke, rounded", "kind": "outline",
                "primary": True},
    "iconoir-solid": {**_ICONOIR, "variant": "solid", "style": "filled (small set)", "kind": "filled"},
    "heroicons": {**_HERO, "variant": "outline", "style": "outline, 1.5px stroke, rounded", "kind": "outline",
                  "primary": True},
    "heroicons-solid": {**_HERO, "variant": "solid", "style": "filled", "kind": "filled"},
    "bootstrap": {**_BOOT, "variant": "regular", "style": "outline look glyphs, 16px grid", "kind": "outline",
                  "primary": True},
    "bootstrap-fill": {**_BOOT, "variant": "fill", "style": "filled glyphs, 16px grid", "kind": "filled"},
    "material": {**_MAT, "variant": "outlined", "style": "Material Symbols outlined, weight 400", "kind": "outline",
                 "primary": True},
    "material-fill": {**_MAT, "variant": "outlined-fill", "style": "Material Symbols filled, weight 400",
                      "kind": "filled"},
    "material-rounded": {**_MAT, "variant": "rounded", "style": "Material Symbols rounded, weight 400",
                         "kind": "outline"},
    "material-rounded-fill": {**_MAT, "variant": "rounded-fill", "style": "Material Symbols rounded filled",
                              "kind": "filled"},
    "material-sharp": {**_MAT, "variant": "sharp", "style": "Material Symbols sharp, weight 400", "kind": "outline"},
    "material-sharp-fill": {**_MAT, "variant": "sharp-fill", "style": "Material Symbols sharp filled",
                            "kind": "filled"},
    "carbon": {**_CARBON, "variant": "regular", "style": "IBM Carbon glyphs, 32px grid, square ends",
               "kind": "outline", "primary": True},
}
DEFAULT_SET = "lucide"          # what the slides and forge engines draw with
KINDS = {"outline": "outline", "line": "outline", "stroke": "outline", "regular": "outline", "filled": "filled",
         "fill": "filled", "solid": "filled", "glyph": "filled", "duotone": "duotone", "two-tone": "duotone"}

# Business words people ask for, and the words icon authors tag with. Both directions are searched, at a lower
# weight than the query word itself.
SYNONYMS = {
    "money": ["dollar", "currency", "cash", "coin", "banknote", "payment", "finance", "wallet"],
    "finance": ["money", "dollar", "bank", "currency", "chart"],
    "cost": ["money", "dollar", "price", "receipt"], "price": ["tag", "dollar", "money", "receipt"],
    "revenue": ["money", "dollar", "income", "trending", "chart"], "profit": ["trending", "money", "growth"],
    "investment": ["money", "chart", "growth", "piggy", "bank"], "capital": ["bank", "landmark", "money"],
    "valuation": ["gauge", "scale", "calculator", "chart"], "deal": ["handshake", "agreement", "contract"],
    "acquisition": ["handshake", "merge", "building"], "merger": ["merge", "handshake", "git-merge"],
    "growth": ["trending", "up", "chart", "arrow", "increase"], "decline": ["trending", "down", "decrease"],
    "risk": ["alert", "warning", "triangle", "shield"], "safety": ["shield", "check", "secure"],
    "security": ["shield", "lock", "key"], "compliance": ["shield", "check", "scale", "law"],
    "legal": ["scale", "gavel", "law", "contract"], "law": ["scale", "gavel", "legal"],
    "contract": ["file", "signature", "document", "pen"], "document": ["file", "page", "paper"],
    "report": ["file", "chart", "document", "clipboard"], "data": ["database", "chart", "table", "server"],
    "analytics": ["chart", "graph", "bar", "pie"], "chart": ["graph", "bar", "pie", "analytics"],
    "people": ["users", "user", "group", "team"], "team": ["users", "group", "people"],
    "customer": ["user", "users", "person", "client"], "employee": ["user", "badge", "id"],
    "workforce": ["users", "people", "team"], "hiring": ["user", "plus", "briefcase"],
    "time": ["clock", "timer", "hourglass", "calendar"], "deadline": ["calendar", "clock", "alarm"],
    "schedule": ["calendar", "clock"], "meeting": ["calendar", "users", "video"],
    "email": ["mail", "envelope", "inbox"], "message": ["chat", "mail", "bubble"], "phone": ["call", "mobile"],
    "idea": ["lightbulb", "bulb", "sparkles"], "innovation": ["lightbulb", "rocket", "sparkles"],
    "strategy": ["target", "chess", "compass", "map"], "goal": ["target", "flag", "trophy"],
    "success": ["trophy", "award", "check", "star"], "launch": ["rocket", "send"],
    "technology": ["cpu", "chip", "server", "code"], "ai": ["cpu", "brain", "sparkles", "bot"],
    "cloud": ["server", "upload"], "software": ["code", "app", "window"], "code": ["terminal", "brackets"],
    "global": ["globe", "world", "earth", "map"], "region": ["globe", "map", "pin"], "location": ["pin", "map"],
    "industry": ["factory", "building", "warehouse"], "manufacturing": ["factory", "gear", "tool"],
    "logistics": ["truck", "package", "box", "warehouse"], "shipping": ["truck", "package", "ship"],
    "supply": ["truck", "package", "box"], "retail": ["store", "shopping", "cart"], "sales": ["cart", "tag",
    "trending", "handshake"], "marketing": ["megaphone", "speakerphone", "target"],
    "health": ["heart", "pulse", "medical", "stethoscope"], "energy": ["bolt", "zap", "battery", "plug"],
    "environment": ["leaf", "tree", "plant", "recycle"], "sustainability": ["leaf", "recycle", "plant"],
    "education": ["school", "graduation", "book"], "research": ["search", "flask", "microscope", "book"],
    "search": ["magnifier", "find", "zoom"], "settings": ["gear", "cog", "adjustments", "sliders"],
    "process": ["workflow", "flow", "steps", "arrows"], "integration": ["puzzle", "plug", "link"],
    "partnership": ["handshake", "link", "users"], "scale": ["maximize", "expand", "layers"],
    "quality": ["badge", "check", "star", "award"], "insight": ["eye", "lightbulb", "chart"],
    "warning": ["alert", "triangle", "exclamation"], "question": ["help", "info"],
}

# consulting, finance, deal and industry vocabulary (merged into SYNONYMS below)
_MORE = {
    # technology and infrastructure
    "datacenter": ["server", "database", "rack", "storage", "cloud", "building"], "datacentre": ["datacenter"],
    "server": ["database", "rack", "storage"], "gpu": ["cpu", "chip", "processor", "graphics", "card", "memory"],
    "semiconductor": ["chip", "cpu", "microchip", "circuit", "processor", "memory"], "chip": ["cpu", "microchip",
    "circuit", "processor"], "hardware": ["cpu", "server", "chip", "device", "monitor"],
    "compute": ["cpu", "server", "chip", "computer"], "ai": ["robot", "neural", "network", "sparkle", "magic", "llm"],
    "llm": ["brain", "bot", "sparkles", "chat", "message"], "genai": ["sparkles", "magic", "brain", "bot", "wand"],
    "automation": ["robot", "bot", "cog", "gear", "workflow", "bolt"], "robot": ["bot", "robotic", "arm"],
    "cloud": ["network", "storage", "sync", "cloud"], "saas": ["cloud", "window", "app", "server"],
    "platform": ["layers", "stack", "server", "window"], "api": ["code", "plug", "link", "brackets", "webhook"],
    "network": ["share", "nodes", "connection", "wifi", "globe", "router"], "iot": ["wifi", "sensor", "network",
    "device", "router"], "quantum": ["atom", "orbit", "cpu"], "storage": ["database", "drive", "archive", "box"],
    "backup": ["database", "archive", "refresh", "save"], "digital": ["monitor", "laptop", "smartphone", "cloud"],
    "telecom": ["antenna", "tower", "signal", "phone", "satellite", "radio"], "satellite": ["antenna", "orbit", "space"],
    "cybersecurity": ["shield", "lock", "key", "security", "fingerprint", "bug", "firewall"],
    "cyber": ["shield", "lock", "security", "bug"], "hack": ["bug", "skull", "alert", "lock", "code"],
    "fraud": ["alert", "mask", "incognito", "shield", "detective"], "identity": ["fingerprint", "id", "user",
    "badge", "key"], "privacy": ["lock", "shield", "eye", "key"], "blockchain": ["link", "chain", "block", "cube",
    "network"], "crypto": ["bitcoin", "coin", "currency", "blockchain", "wallet"],
    # energy and resources
    "energy": ["power", "electricity", "solar", "wind", "lightning", "flash"], "power": ["bolt", "plug", "zap",
    "battery", "lightning", "electricity"], "electricity": ["bolt", "plug", "zap", "power", "lightning"],
    "utility": ["bolt", "droplet", "plug", "flame", "tower"], "solar": ["sun", "panel", "bolt", "energy"],
    "wind": ["wind", "fan", "turbine"], "renewable": ["sun", "wind", "leaf", "recycle", "solar"],
    "nuclear": ["atom", "radiation", "radioactive"], "oil": ["droplet", "barrel", "fuel", "gas", "pump"],
    "gas": ["fuel", "flame", "pump"], "battery": ["bolt", "charging", "power"], "ev": ["car", "battery", "plug",
    "charging", "bolt"], "mining": ["pickaxe", "mountain", "shovel", "diamond", "gem"], "water": ["droplet", "wave"],
    "carbon": ["co2", "leaf", "cloud", "factory", "emission", "smoke", "footprint"], "emission": ["smoke", "factory",
    "cloud", "co2"], "climate": ["thermometer", "globe", "leaf", "cloud", "sun", "earth"],
    "esg": ["leaf", "recycle", "users", "scale", "plant", "globe"],
    # industries
    "factory": ["industry", "building", "manufacturing", "warehouse"], "industrial": ["factory", "gear", "building"],
    "warehouse": ["box", "package", "building", "storage"], "inventory": ["box", "package", "warehouse", "barcode",
    "stack"], "procurement": ["cart", "shopping", "package", "handshake", "receipt"], "freight": ["truck", "ship",
    "package", "container", "train"], "port": ["ship", "anchor", "container", "crane"], "container": ["box", "package",
    "ship"], "distribution": ["truck", "network", "package", "share"], "supplier": ["truck", "factory", "handshake",
    "package"], "logistics": ["route", "delivery", "container", "forklift"], "supply": ["chain", "warehouse",
    "factory", "route"], "transport": ["truck", "bus", "train", "plane", "car", "ship"], "aviation": ["plane",
    "airplane", "flight"], "automotive": ["car", "vehicle", "engine"], "retail": ["shop", "bag", "storefront",
    "receipt", "basket"], "ecommerce": ["cart", "shopping", "store", "package", "browser"], "consumer": ["shopping",
    "cart", "bag", "user", "store"], "food": ["utensils", "fork", "cutlery", "restaurant", "apple"],
    "agriculture": ["plant", "wheat", "tractor", "farm", "leaf", "sprout"], "chemical": ["flask", "beaker", "atom",
    "tube"], "pharma": ["pill", "capsule", "medicine", "flask", "syringe", "dna"], "biotech": ["dna", "flask",
    "microscope", "molecule"], "healthcare": ["hospital", "medical", "stethoscope", "heart", "pulse", "health"],
    "hospital": ["medical", "cross", "building", "bed"], "patient": ["user", "bed", "heart", "stethoscope"],
    "medicine": ["pill", "capsule", "syringe", "cross"], "insurance": ["shield", "umbrella", "policy", "protection",
    "lifebuoy"], "claim": ["file", "shield", "clipboard"], "bank": ["landmark", "building", "vault", "money",
    "columns", "safe"], "banking": ["bank", "landmark", "vault", "money"], "fintech": ["card", "smartphone",
    "wallet", "coin", "chart"], "property": ["home", "building", "house", "key"], "construction": ["crane", "hammer",
    "helmet", "building", "brick"], "infrastructure": ["building", "bridge", "road", "tower", "server"],
    "government": ["landmark", "building", "flag", "scale", "capitol", "gavel"], "public": ["landmark", "users",
    "building"], "media": ["play", "video", "newspaper", "tv", "camera"], "telehealth": ["video", "stethoscope",
    "phone"],
    # deals, finance and markets
    "ipo": ["bell", "rocket", "chart", "stock", "market", "trending", "launch"], "listing": ["bell", "chart", "stock"],
    "debt": ["credit", "card", "loan", "receipt", "scale", "money"], "loan": ["money", "bank", "handshake", "coins"],
    "credit": ["card", "money", "score"], "equity": ["pie", "chart", "share", "stock", "ownership"],
    "dividend": ["coins", "money", "pie", "percent", "cash"], "stock": ["chart", "trending", "candlestick", "market"],
    "share": ["pie", "chart", "stock"], "market": ["store", "chart", "trending", "shop"], "portfolio": ["briefcase",
    "pie", "layers", "folder"], "fund": ["piggy", "bank", "money", "coins", "wallet"], "budget": ["calculator",
    "wallet", "money", "pie", "receipt"], "forecast": ["trending", "chart", "telescope", "line"], "pricing": ["tag",
    "price", "dollar"], "inflation": ["trending", "percent", "balloon", "money"], "interest": ["percent", "rate",
    "money"], "rate": ["percent"], "currency": ["dollar", "euro", "coin", "money", "exchange"], "payment": ["card",
    "credit", "wallet", "cash", "money"], "invoice": ["receipt", "file", "bill", "document"], "tax": ["receipt",
    "percent", "calculator", "file", "landmark"], "taxes": ["tax"], "audit": ["clipboard", "check", "search", "file",
    "list"], "diligence": ["search", "check", "magnifier", "clipboard", "file"], "negotiation": ["handshake", "chat",
    "scale", "users"], "savings": ["piggy", "coins", "wallet", "percent"], "margin": ["percent", "chart",
    "trending"], "ebitda": ["chart", "money", "calculator", "bar"], "cashflow": ["cash", "money", "arrows", "flow"],
    "liquidity": ["droplet", "water", "cash", "money"], "leverage": ["scale", "lever", "weight", "balance"],
    "capex": ["building", "factory", "money", "construction"], "opex": ["receipt", "money", "cog", "gear"],
    "earnings": ["money", "chart", "trending", "dollar", "coins"], "income": ["money", "dollar", "wallet", "coins"],
    "expense": ["receipt", "wallet", "card", "money"], "loss": ["trending", "down", "minus", "decline"],
    "volatility": ["activity", "pulse", "wave", "chart"], "uncertainty": ["question", "cloud", "fog", "help"],
    "crisis": ["alert", "fire", "siren", "warning"], "recovery": ["refresh", "trending", "heart", "lifebuoy"],
    "resilience": ["shield", "anchor", "lifebuoy", "heart"], "investor": ["user", "money", "chart", "briefcase",
    "piggy"], "shareholder": ["users", "pie", "chart", "coins"], "stakeholder": ["users", "group", "network",
    "handshake"], "exit": ["door", "logout", "arrow"], "synergy": ["puzzle", "link", "merge", "plus"],
    "roi": ["percent", "chart", "trending", "money", "coins"], "benchmark": ["chart", "bar", "gauge", "ruler",
    "trophy"], "comparison": ["scale", "columns", "compare", "bar"],
    # regulation and governance
    "regulation": ["scale", "gavel", "law", "document", "rule", "policy", "landmark", "compliance"],
    "regulatory": ["regulation"], "antitrust": ["scale", "gavel", "law", "landmark", "handshake"],
    "policy": ["file", "scale", "shield", "gavel", "document"], "governance": ["landmark", "building", "scale",
    "gavel", "users", "shield"], "board": ["users", "meeting", "chair", "presentation", "table", "group"],
    "director": ["user", "briefcase", "chair"], "vote": ["check", "ballot", "hand", "box"], "approval": ["check",
    "thumbs", "stamp", "badge"], "ceo": ["user", "crown", "briefcase", "tie"], "executive": ["briefcase", "user",
    "tie", "crown"], "leadership": ["crown", "flag", "user", "star", "compass"],
    # planning and performance
    "kpi": ["gauge", "dashboard", "chart", "target", "speedometer", "meter"], "metric": ["gauge", "dashboard",
    "chart"], "okr": ["target", "goal", "flag", "key", "checklist"], "objective": ["target", "flag", "goal"],
    "roadmap": ["map", "route", "signpost", "milestone", "timeline", "road", "flag"], "milestone": ["flag",
    "signpost", "calendar", "trophy", "check"], "timeline": ["calendar", "clock", "milestone", "history", "line"],
    "project": ["clipboard", "kanban", "folder", "list", "checklist"], "task": ["check", "list", "clipboard"],
    "priority": ["flag", "star", "alert", "arrow"], "vision": ["eye", "telescope", "binoculars", "mountain"],
    "mission": ["flag", "target", "rocket", "compass"], "value": ["gem", "diamond", "star", "money", "tag"],
    "decision": ["signpost", "split", "scale", "fork", "question"], "scenario": ["branch", "split", "layers", "map"],
    "competition": ["trophy", "chess", "flag", "medal", "users"], "competitor": ["chess", "users", "trophy"],
    "advantage": ["trophy", "star", "arrow"], "moat": ["shield", "castle", "lock"], "efficiency": ["gauge", "bolt",
    "speedometer", "timer", "rocket"], "productivity": ["gauge", "bolt", "check", "timer", "rocket"],
    "speed": ["gauge", "bolt", "rocket", "timer", "fast"], "agile": ["refresh", "cycle", "arrows", "rocket"],
    "transformation": ["refresh", "arrows", "transform", "rocket", "change", "cycle"],
    "satisfaction": ["smile", "thumbs", "star", "heart"], "feedback": ["chat", "message", "star", "comment"],
    "survey": ["clipboard", "list", "checklist", "chart"], "advertising": ["megaphone", "speakerphone", "billboard"],
    "brand": ["tag", "badge", "star", "megaphone"], "communication": ["chat", "message", "megaphone", "mail"],
    "presentation": ["easel", "chart", "screen", "board"], "talent": ["user", "star", "award", "briefcase"],
    "training": ["school", "graduation", "book", "presentation"], "diversity": ["users", "people", "heart"],
    "culture": ["heart", "users", "smile"],
}
# multi-word and punctuated terms (matched on the whole query, before it is split into words)
PHRASES = {
    "supply chain": ["truck", "warehouse", "factory", "package", "route", "container", "link"],
    "power grid": ["bolt", "tower", "transmission", "electricity", "plug", "utility", "pylon"],
    "data center": ["server", "database", "rack", "storage", "cloud"], "data centre": ["server", "database", "rack",
    "storage", "cloud"], "m&a": ["handshake", "merge", "building", "briefcase", "puzzle"],
    "mergers and acquisitions": ["handshake", "merge", "building", "briefcase"],
    "private equity": ["briefcase", "money", "chart", "pie", "building"], "venture capital": ["rocket", "money",
    "seedling", "chart"], "real estate": ["building", "home", "house", "key", "city"], "due diligence": ["search",
    "check", "clipboard", "file", "magnifier"], "machine learning": ["brain", "cpu", "robot", "network", "chart"],
    "artificial intelligence": ["brain", "cpu", "robot", "sparkles", "bot"], "cash flow": ["cash", "money",
    "arrows", "coins"], "net zero": ["leaf", "recycle", "co2", "globe"], "customer experience": ["smile", "heart",
    "user", "star"], "cost reduction": ["scissors", "trending", "down", "money", "piggy"], "interest rate":
    ["percent", "chart", "bank"], "balance sheet": ["scale", "file", "table", "spreadsheet"], "income statement":
    ["file", "chart", "receipt"], "go to market": ["rocket", "megaphone", "target", "store"],
    "climate change": ["thermometer", "globe", "leaf", "sun"], "electric vehicle": ["car", "battery", "plug",
    "charging"], "renewable energy": ["sun", "wind", "solar", "leaf"], "human resources": ["users", "id", "badge",
    "briefcase"], "board of directors": ["users", "presentation", "table", "chair"], "return on investment":
    ["percent", "chart", "trending", "coins"], "capital markets": ["chart", "candlestick", "bank", "trending"],
    "health care": ["hospital", "medical", "stethoscope", "heart"], "oil and gas": ["droplet", "barrel", "flame",
    "pump"], "public sector": ["landmark", "building", "flag"], "financial services": ["bank", "landmark", "money",
    "card"], "cyber security": ["shield", "lock", "key", "bug"], "risk management": ["shield", "alert", "gauge"],
    "value creation": ["trending", "gem", "chart", "plus"], "deal flow": ["handshake", "funnel", "arrows"],
}
for _k, _v in _MORE.items():
    SYNONYMS[_k] = list(dict.fromkeys(SYNONYMS.get(_k, []) + _v))
MODIFIERS = {"off", "x", "slash", "minus", "plus", "question", "exclamation", "dashed", "check", "cancel", "pause",
             "up", "down", "left", "right", "share", "star", "heart", "search", "bolt", "cog", "code", "dollar",
             "pin", "spark", "filled", "edit", "move", "sad", "dot", "due", "exclamation", "2", "3", "4", "5",
             # named currencies rank below the generic money icons unless asked for ("yen" still finds the yen)
             "yen", "yuan", "lira", "pound", "ruble", "rupee", "won", "peso", "franc", "krona", "shekel", "naira",
             "dong", "baht", "real", "rand", "zloty", "lari", "manat", "tenge", "hryvnia", "kip", "riel", "taka",
             "tugrik", "rupiah", "renminbi", "frank", "afghani", "dirham", "riyal", "bahraini", "cent", "guarani"}


def _stem(w: str) -> str:
    """A tiny English stemmer: enough that 'charts' finds 'chart' and 'companies' finds 'company'."""
    if len(w) > 4 and w.endswith("ies"):
        return w[:-3] + "y"
    if len(w) > 5 and w.endswith("ing"):
        return w[:-3]
    if len(w) > 3 and w.endswith("s") and not w.endswith("ss"):
        return w[:-1]
    return w


def _words(text: str) -> list[str]:
    return [_stem(w) for w in re.findall(r"[a-z0-9]+", str(text).lower())]


@lru_cache(maxsize=1)
def _meanings() -> dict:
    """ICON_MEANINGS from the forge renderer (meaning word -> Lucide icon), read without importing the module."""
    src = ENGINES / "forge" / "render.py"
    try:
        for node in ast.parse(src.read_text(encoding="utf-8")).body:
            if isinstance(node, ast.Assign) and any(getattr(t, "id", "") == "ICON_MEANINGS" for t in node.targets):
                return {str(k): str(v) for k, v in ast.literal_eval(node.value).items()}
    except (OSError, SyntaxError, ValueError):
        pass
    return {}


@lru_cache(maxsize=None)
def _bundle(file: str) -> dict:
    """A bundled family: {"variants": {style: {name: svg}}, "tags": {name: {"c", "t"}}, "version"}."""
    with gzip.open(file, "rb") as f:
        return json.loads(f.read().decode("utf-8"))


def _icons(set: str) -> dict:
    """name -> SVG text for a bundled set (empty for a folder set)."""
    info = SETS[set]
    if "bundle" not in info:
        return {}
    try:
        return _bundle(str(info["bundle"]))["variants"].get(info["variant"], {})
    except (OSError, ValueError):
        return {}


@lru_cache(maxsize=None)
def _tags(set: str) -> dict:
    info = SETS[set]
    if "tags" in info:
        try:
            return json.loads(info["tags"].read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
    try:
        return _bundle(str(info["bundle"])).get("tags", {})
    except (OSError, ValueError):
        return {}


@lru_cache(maxsize=1)
def _shared_tags() -> dict:
    """Tags of same-named icons in the tagged sets (Lucide, Tabler, Phosphor), lent to the sets whose packages ship
    no tags (Iconoir, Heroicons, Bootstrap, Material), so "money" also finds their "banknotes" or "payments"."""
    out: dict = {}
    for s in ("phosphor", "tabler", "lucide"):
        for name, t in _tags(s).items():
            out.setdefault(name, t)
    return out


def _cache_path(set: str, name: str) -> Path:
    return CACHE / f"{set}-{SETS[set]['version']}" / f"{name}.svg"


@lru_cache(maxsize=None)
def index(set: str | None = None) -> tuple[dict, ...]:
    """Every icon of one set (or all sets): name, set, category, tags and the SVG path. For a bundled set the path is
    where path() writes the SVG on first use."""
    if set is None:
        return tuple(r for s in SETS for r in index(s))
    set = _check_set(set)
    info = SETS[set]
    tags = _tags(set)
    lend = _shared_tags() if not tags else {}
    if "dir" in info:
        items = [(p.stem, str(p)) for p in sorted(info["dir"].glob("*.svg"))]
    else:
        items = [(n, str(_cache_path(set, n))) for n in sorted(_icons(set))]
    rows = []
    for name, p in items:
        t = tags.get(name) or lend.get(name) or []
        cat, tl = (t.get("c", ""), t.get("t", [])) if isinstance(t, dict) else ("", t)
        rows.append({"name": name, "set": set, "category": cat, "tags": list(tl), "path": p})
    return tuple(rows)


def _check_set(set: str | None) -> str:
    s = (set or DEFAULT_SET).strip().lower()
    if s not in SETS:
        raise ValueError(f"Unknown icon set {set!r}. Sets: {', '.join(SETS)}")
    return s


def _kind(style: str | None) -> str | None:
    if not style:
        return None
    k = KINDS.get(str(style).strip().lower())
    if not k:
        raise ValueError(f"Unknown icon style {style!r}. Styles: outline, filled, duotone")
    return k


def search_sets(style: str | None = None) -> list[str]:
    """The sets searched when no set is named: each family's main style, or its first set of `style`."""
    k = _kind(style)
    if not k:
        return [s for s, v in SETS.items() if v.get("primary")]
    out, seen = [], set()
    for s, v in SETS.items():
        if v["kind"] == k and v["family"] not in seen:
            seen.add(v["family"])
            out.append(s)
    return out


@lru_cache(maxsize=None)
def _prepared(set: str) -> tuple:
    """Per icon: (row, name words, tag words, category words), computed once per set."""
    out = []
    for r in index(set):
        tag_words = {w for t in r["tags"] for w in _words(t)}
        out.append((r, _words(r["name"].replace("-", " ")), tag_words, frozenset(_words(r["category"]))))
    return tuple(out)


def _terms(query: str) -> tuple[list[str], dict[str, float]]:
    """The query's own words (weight 1), its phrase synonyms (0.75) and word synonyms (0.5)."""
    raw = " " + re.sub(r"[\s_-]+", " ", str(query).lower()).strip() + " "
    phrase_syn = [syn for p, syns in PHRASES.items() if f" {p} " in raw for syn in syns]
    q = _words(query)
    if phrase_syn:
        q = [w for w in q if len(w) > 1]                  # "m&a" -> no stray "m" and "a"
    terms: dict[str, float] = {w: 1.0 for w in q}
    for syn in phrase_syn:
        for s in _words(syn.replace("-", " ")):
            terms.setdefault(s, 0.75)
    for w in q:
        for raw_w in (w, w + "s"):
            for syn in SYNONYMS.get(raw_w, []):
                for s in _words(syn.replace("-", " ")):
                    terms.setdefault(s, 0.5)
    return q or list(terms)[:1], terms


def _score(prep, q: list[str], terms: dict, meaning: dict, phrase: str) -> float:
    r, nw, tw, cw = prep
    score, hit = 0.0, set()
    for t, w in terms.items():
        if nw and t == nw[0]:
            s = 4.0
        elif t in nw:
            s = 3.0
        elif t in tw:
            s = 2.0
        elif len(t) >= 4 and any(x.startswith(t) for x in nw):
            s = 1.0
        elif t in cw:
            s = 0.5
        else:
            continue
        score += s * w
        if w == 1.0:
            hit.add(t)
    if r["set"] == "lucide" and r["name"] in meaning:
        score += 6.0
    if not score:
        return 0.0
    if phrase and (phrase == " ".join(nw) or phrase in {" ".join(_words(t)) for t in r["tags"]}):
        score += 5.0
    score += 2.0 * len(hit) - 0.6 * sum(1 for x in nw[1:] if x in MODIFIERS) - 0.15 * len(nw)
    return round(score, 2)


def search(query: str, set: str | None = None, limit: int = 12, style: str | None = None) -> list[dict]:
    """Icons that fit `query` (a word or short phrase such as "money", "supply chain", "M&A"), best first.
    `set` restricts the search to one set. Without it, each family's main style is searched, or with `style`
    ("outline", "filled", "duotone") each family's first set of that style. Each hit carries its set, licence and
    style; `path` is a file on disk (bundled sets are written to the cache folder for the hits returned)."""
    q, terms = _terms(query)
    if not q:
        return []
    names = {_stem(w) for w in q}
    meaning = {v for k, v in _meanings().items() if _stem(k) in names}
    phrase = " ".join(q)
    sets = [_check_set(set)] if set else search_sets(style)
    rows = []
    for s in sets:
        info = SETS[s]
        for prep in _prepared(s):
            sc = _score(prep, q, terms, meaning, phrase)
            if sc > 0:
                r = prep[0]
                rows.append({"name": r["name"], "set": s, "score": sc, "category": r["category"],
                             "tags": r["tags"][:8], "path": r["path"], "license": info["license"],
                             "style": info["kind"], "family": info["family"]})
    rows.sort(key=lambda r: (-r["score"], len(r["name"]), r["name"]))
    rows = rows[:max(1, int(limit))]
    for r in rows:
        if "bundle" in SETS[r["set"]]:
            p = path(r["name"], r["set"])
            r["path"] = str(p) if p else ""
    return rows


def best_set(queries: list[str], style: str | None = None) -> str:
    """The one set that serves all of a deck's meanings best (sum of each meaning's top score), among each family's
    main style (or its `style` set)."""
    def total(s):
        return sum((search(q, set=s, limit=1) or [{"score": 0}])[0]["score"] for q in queries)
    return max(search_sets(style), key=lambda s: (total(s), s == DEFAULT_SET))


def _split(name: str, set: str | None) -> tuple[str, str | None]:
    if ":" in name and set is None:
        set, name = name.split(":", 1)
    return name.strip().lower(), set


def _lookup(name: str, s: str) -> str | None:
    """The icon's name as stored in set `s` (Material's "attach_money" is stored as "attach-money"), or None."""
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]*", name):
        return None
    for n in dict.fromkeys((name, name.replace("_", "-"))):
        if "dir" in SETS[s]:
            if (SETS[s]["dir"] / f"{n}.svg").is_file():
                return n
        elif n in _icons(s):
            return n
    return None


def svg(name: str, set: str | None = None) -> str | None:
    """The SVG text of an icon (strokes and fills in currentColor). `name` may carry its set ("phosphor:bank");
    without a set, Lucide is tried first, then Tabler and the bundled sets in SETS order."""
    name, set = _split(name, set)
    for s in ([_check_set(set)] if set else list(SETS)):
        n = _lookup(name, s)
        if n:
            if "dir" in SETS[s]:
                return (SETS[s]["dir"] / f"{n}.svg").read_text(encoding="utf-8")
            return _icons(s)[n]
    return None


def path(name: str, set: str | None = None) -> Path | None:
    """The SVG file of an icon. `name` may carry its set ("tabler:coin"); without a set, Lucide is tried first.
    Icons of a bundled set are written once to the cache folder (MESHPOOL_ICON_CACHE, default <temp>/meshpool_icons)."""
    name, set = _split(name, set)
    for s in ([_check_set(set)] if set else list(SETS)):
        n = _lookup(name, s)
        if not n:
            continue
        if "dir" in SETS[s]:
            return SETS[s]["dir"] / f"{n}.svg"
        out = _cache_path(s, n)
        if not out.is_file():
            out.parent.mkdir(parents=True, exist_ok=True)
            tmp = out.with_suffix(f".{os.getpid()}.tmp")
            tmp.write_text(_icons(s)[n], encoding="utf-8")
            os.replace(tmp, out)
        return out
    return None


def sets(style: str | None = None) -> list[dict]:
    """The bundled sets with licence, source, style and icon count (only those of `style` when given)."""
    k = _kind(style)
    return [{"set": s, "count": len(index(s)), "license": v["license"], "license_file": str(v["license_file"]),
             "version": v["version"], "source": v["source"], "style": v["style"], "kind": v["kind"],
             "family": v["family"], "primary": bool(v.get("primary"))}
            for s, v in SETS.items() if not k or v["kind"] == k]
