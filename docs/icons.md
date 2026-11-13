# Icon library

Meshpool bundles two open-licence outline icon sets and one index over both, so agents (and the slides and forge
engines) can find an icon by meaning instead of guessing file names.

| Set | Icons | Licence | Where | Source |
|---|---|---|---|---|
| `lucide` (default) | 1,539 | ISC | `app/engines/slides/icons/` (`LICENSE-lucide.txt`) | lucide-static 0.460.0 |
| `tabler` | 4,855 | MIT | `app/engines/icons/tabler/` (`LICENSE-tabler.txt`) | @tabler/icons 3.51.0, outline style |

Both sets are 24 x 24 outline icons with a 2 px round stroke in `currentColor`, so they recolour the same way.
Tabler's *Brand* category (company logos) is left out: logos are trademarks, not general-purpose icons. Search
tags come from each package's own metadata (`app/engines/icons/tags-lucide.json`, `tags-tabler.json`).

**Use one set per deck.** Mixing sets on one slide makes stroke ends and corner radii look inconsistent.

## Python

```python
from engines import icons

icons.search("money")                 # ranked across both sets
icons.search("money", set="tabler")   # one set only
icons.path("tabler:coin")             # Path to the SVG, or None
icons.path("users")                   # no set given: Lucide first, then Tabler
icons.best_set(["money", "growth", "risk"])   # the set that covers a deck's meanings best
icons.sets()                          # sets with count, licence, version and source
```

Each result is `{"name", "set", "score", "category", "tags", "path"}`.

Ranking: a query word that is the icon's first name word scores highest, then any name word, then a tag, then a
name prefix, then the category. Synonyms (`SYNONYMS` in `engines/icons/__init__.py`, e.g. *money* -> dollar, cash,
coin, banknote) count at half weight, plurals are folded (*charts* -> chart), and the meanings the forge renderer
already uses (`ICON_MEANINGS` in `engines/forge/render.py`, read without importing it) get a boost in Lucide.
Variants such as `-off`, `-plus`, `-edit` rank below the plain icon.

## Agents: `find_icon`

The Meshpool Office MCP server (`meshpool_harness/agent/office_mcp_server.py`) has a read-only tool:

```
find_icon(query, set?, limit?)
```

It returns `set:name`, the score and the SVG path for each match, best first. It runs without an approval prompt
(`mcp__meshpool_office__find_icon` is in `SAFE_MCP`).

## Adding a set

1. Bundle the SVGs in their own folder with the licence text, after checking the licence allows redistribution.
2. Write `tags-<set>.json` (`{"icon-name": ["tag", ...]}` or `{"icon-name": {"c": "category", "t": [...]}}`).
3. Add an entry to `SETS` in `app/engines/icons/__init__.py` and a row to the table above and to
   `THIRD_PARTY_NOTICES.md`.
