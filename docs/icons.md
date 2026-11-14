# Icon library

Meshpool bundles nine open-licence icon families (22 sets, about 46,000 icons) and one index over all of them, so
agents (and the slides and forge engines) can find an icon by meaning instead of guessing file names. Each style of
a family is its own set, so a deck can stay in one style.

| Set(s) | Icons each | Style | Licence | Source |
|---|---|---|---|---|
| `lucide` (default) | 1,539 | outline, 2 px stroke | ISC | lucide-static 0.460.0, `app/engines/slides/icons/` |
| `tabler` | 4,855 | outline, 2 px stroke | MIT | @tabler/icons 3.51.0, `app/engines/icons/tabler/` |
| `tabler-filled` | 1,019 | filled | MIT | @tabler/icons 3.51.0 |
| `phosphor`, `phosphor-light`, `phosphor-thin`, `phosphor-bold` | 1,434 | outline look, four weights | MIT | @phosphor-icons/core 2.1.1 |
| `phosphor-fill`, `phosphor-duotone` | 1,434 | filled; duotone (outline over a 20% tint) | MIT | @phosphor-icons/core 2.1.1 |
| `iconoir` / `iconoir-solid` | 1,298 / 272 | outline 1.5 px / filled | MIT | iconoir 7.12.2 |
| `heroicons` / `heroicons-solid` | 324 | outline 1.5 px / filled (24 px) | MIT | heroicons 2.2.0 |
| `bootstrap` / `bootstrap-fill` | 1,323 / 665 | outline-look glyphs / filled (16 px grid) | MIT | bootstrap-icons 1.13.2 |
| `material`, `material-rounded`, `material-sharp` | 3,885 | Material Symbols, weight 400 | Apache-2.0 | @material-symbols/svg-400 0.49.0 |
| `material-fill`, `material-rounded-fill`, `material-sharp-fill` | 3,885 | the same, filled | Apache-2.0 | @material-symbols/svg-400 0.49.0 |
| `carbon` | 2,353 | IBM Carbon glyphs, 32 px grid | Apache-2.0 | @carbon/icons 11.92.0 |

Lucide and Tabler outline ship as loose SVG files. Every other set is one compressed bundle per family in
`app/engines/icons/bundles/<family>.json.gz` (name -> minified SVG for each style, plus the package's own tags and
categories), about 5.5 MB for all of them, next to each family's licence file. The SVGs are normalised when the
bundle is built: viewBox kept, width/height dropped, every colour set to `currentColor` so the icons recolour.

**No company logos.** Brand and logo icons are left out of every set (Tabler's and Phosphor's Brand categories,
Carbon's Brand category and IBM product marks, and the logos in Bootstrap, Iconoir and Material such as GitHub,
Apple, Google, Android): logos are trademarks, not general-purpose icons.

**Use one set per deck.** Mixing sets on one slide makes stroke weights, ends and corner radii look inconsistent.

Tags come from each package's own metadata where it ships them (Lucide, Tabler, Phosphor, Carbon). Iconoir,
Heroicons, Bootstrap and Material ship none; their icons borrow the tags of the same-named icon in Phosphor, Tabler
or Lucide.

## Python

```python
from engines import icons

icons.search("money")                       # each family's main style, best first
icons.search("money", set="phosphor-fill")  # one set only
icons.search("money", style="filled")       # the filled set of every family
icons.svg("phosphor-fill:bank")             # the SVG text (currentColor)
icons.path("tabler:coin")                   # Path to the SVG; bundled sets are written to a cache folder
icons.path("material:attach_money")         # Material's own underscore names work too
icons.path("users")                         # no set given: Lucide first, then Tabler, then the bundles
icons.best_set(["money", "growth", "risk"]) # the set that covers a deck's meanings best
icons.sets()                                # sets with count, licence, version, source and style
```

Each result is `{"name", "set", "family", "score", "category", "tags", "path", "license", "style"}` (`style`:
outline, filled or duotone). The cache folder for bundled sets is `MESHPOOL_ICON_CACHE`, default
`<temp>/meshpool_icons`.

Ranking: a query word that is the icon's first name word scores highest, then any name word, then a tag, then a
name prefix, then the category. Synonyms (`SYNONYMS` in `engines/icons/__init__.py`, e.g. *money* -> dollar, cash,
coin, banknote; consulting, deal, finance and industry words such as *datacentre*, *GPU*, *IPO*, *ESG*, *KPI*,
*antitrust*) count at half weight, and phrases (`PHRASES`: *supply chain*, *power grid*, *M&A*, *due diligence*)
at three quarters. Plurals are folded (*charts* -> chart), and the meanings the forge renderer already uses
(`ICON_MEANINGS` in `engines/forge/render.py`, read without importing it) get a boost in Lucide. Variants such as
`-off`, `-plus`, `-edit` and named currencies (*yen*, *rupee*) rank below the plain icon.

## Decks: `icon_set`

The forge renderer draws Lucide icons as PNG by default. Give a deck one set with `render(..., icon_set="phosphor")`,
`theme={"icon_set": "phosphor"}`, the forge engine option `icon_set`, or the request ("phosphor icons",
"icon set: material-rounded"; `make_deck` has an `icon_set` field). Each row meaning (*money*, *risk*,
*congestion*) is then found in that set: the set's own icon of that name, else a search for the meaning together
with the Lucide icon it maps to and its tags. The icon is embedded as SVG (PowerPoint's `asvg:svgBlip` extension on
the picture) with a PNG fallback, so it stays crisp in PowerPoint 2016 and later and in LibreOffice, and the PNG
shows in older readers. The fallback PNG is drawn with Pillow (`engines/icons/raster.py`), so it needs no Cairo
library (missing on most Mac and Windows installs); Lucide's own PNGs keep the renderer's mask/Cairo path. The
deck rules allow every library set (`allowed_sets` in `engines/forge/rules.yaml`).

## Agents: `find_icon`

The Meshpool Office MCP server (`meshpool_harness/agent/office_mcp_server.py`) has a read-only tool:

```
find_icon(query, set?, style?, limit?, list_sets?)
```

It returns `set:name`, the score, style, licence and the SVG path for each match, best first; `list_sets=true`
lists the sets. It runs without an approval prompt (`mcp__meshpool_office__find_icon` is in `SAFE_MCP`).

## Adding a set

1. Check the licence in the package itself allows redistribution (MIT, ISC, Apache-2.0 and similar; no NC/ND or
   custom terms) and that the package ships the licence text.
2. Fetch it with `npm pack --ignore-scripts <package>@<version>` and unpack it without running anything, then add
   it to `app/engines/icons/build_bundles.py` (styles, brand exclusions, tags) and rebuild the bundles.
3. Copy the licence file into `app/engines/icons/bundles/`, add the sets to `SETS` in
   `app/engines/icons/__init__.py`, to `allowed_sets` in `app/engines/forge/rules.yaml`, to the table above and to
   `THIRD_PARTY_NOTICES.md`.
