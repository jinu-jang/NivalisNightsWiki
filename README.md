# Nivalis Fieldnotes

A static quest and dialogue guide for local analysis of Nivalis Nights.
The frontend is independent of Unity and Python: it loads a catalog and one JSON
file per quest or dialogue. The original identity and icons are fan-made, not
official game artwork.

## Generate and view a local guide

```powershell
python -m venv .venv
& .\.venv\Scripts\python.exe -m pip install -r requirements.txt
& .\.venv\Scripts\python.exe .\export_guide.py
& .\.venv\Scripts\python.exe -m http.server 8765 --bind 127.0.0.1 --directory .\output
```

Open `http://127.0.0.1:8765/`. Use HTTP, rather than double-clicking the HTML:
the app fetches JSON and JavaScript modules. The default export is the complete
**Noodle Bar Introduction** quest, opening Nola Berg's recipe conversation.

```powershell
# Every authored quest and dialogue container in the installed build.
& .\.venv\Scripts\python.exe .\export_guide.py --scope all

# A different quest, or just one dialogue and its quest metadata.
& .\.venv\Scripts\python.exe .\export_guide.py --quest-id Noodle_Bar
& .\.venv\Scripts\python.exe .\export_guide.py --dialogue-id Dlg_2FF9F477
```

Use `--game-dir "D:\SteamLibrary\steamapps\common\Nivalis Nights"` for another
installation. `--include-raw` additionally retains original serialized records
for research. `extract_dialogue.py` remains a compatible CLI entry point.
Extraction never modifies game files or saves. Generated content stays in
Git-ignored `output`, outside the frontend source in `web`.

## Data format

| File | Purpose |
| --- | --- |
| `data\catalog.json` | Titles, coverage, defaults, and relative paths for lazy loading |
| `data\quests\<id>.json` | Objectives, target NPCs, lifecycle scripts, explicit dialogue references, and quest flow |
| `data\dialogues\<id>.json` | Nodes, connections, choices, effects, gates, source IDs, and bounded route analysis |
| `schemas\*.schema.json` | Version 2 JSON Schema contracts |

IDs are strings, including 64-bit Articy identifiers. Connections retain pin
identity, ordering, and whether the destination is an input or output pin.
Quest-dialogue links come from objective selectors and authored containment, not
similar names or a guessed NPC association. Objectives without conversations
remain objectives; authoring order is not presented as guaranteed progression.

The current installed build contains **113 authored quests and 1,569 dialogue
containers**: ordinary, phone, background, repeating, and spot-dependent
conversations. These are asset counts, not a claim that every record is reachable
in gameplay. English localization is preferred. Missing localized lines use
authored English with an explicit warning and `textSource`.

Static exploration is **not save-state simulation**. Conditions and unsupported
template behavior are visible but not evaluated. Literal assignments and
increments are interpreted; other scripts remain unparsed. Recipe selectors are
resolved to real recipe assets. Demeanor describes NPC expressions, not meters.
Complete route lists are bounded to 128 paths / 1,024 nodes per path; cyclic,
oversized, or unfinished graphs remain available with a clear analysis status.
External dialogue links do not automatically execute another conversation.

## GitHub Pages

The app uses relative URLs, so it supports a repository Pages subpath without a
backend, bundler, or CDN. Choose **GitHub Actions** under the repository's Pages
settings and manually run **Publish guide frontend**.

The workflow publishes `web` and its empty-library onboarding by default.
It deliberately does **not** publish locally extracted game text, scripts,
character assets, or saves. Buying the game does not establish redistribution
rights; obtain permission before publishing an extracted data pack.

If you have permission, curate and commit the approved `data` pack into
`published-data` with `catalog.json` at that folder's root. The manual workflow
will combine it with the frontend. Do not include an entire game installation or
save data. No public deployment is performed by running the extractor.

## Checks

```powershell
& .\.venv\Scripts\python.exe -m unittest discover -s tests
```

Core checks use synthetic data. Local integration checks additionally inspect
the generated pack when present, including Nola's observed route, shared Beer
reward, quest link, source identifiers, and catalog/graph integrity.
