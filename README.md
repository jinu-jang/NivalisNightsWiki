# Nivalis Nights Wiki

A static, unofficial wiki foundation for local analysis of Nivalis Nights.
The frontend is independent of Unity and Python: it loads a catalog and one JSON
file per quest or dialogue. The journal-style interface uses game-inspired pale
panels, brass accents, NPC portraits, and colored relationship symbols while
keeping the synchronized conversation graph.

Every conversation starts with a compact outcome summary, also visible above the
expanded map. Compare compatible effect combinations to highlight a real example
path without changing your explored route. Relationship and reward symbols appear
inside map nodes as well as beside replies.

In the **Dialogue** library, search words from NPC speech or player replies,
optionally together with a character name. Results show highlighted source text;
selecting a spoken match opens and inspects that exact map node. Phrase searches
use quotes. The compact text index downloads only when needed and is cached.

This iteration covers quests and dialogue exploration. Character articles, item
pages, and recipe-source lookup are future wiki sections, not implemented pages.
The source-only edition uses original wiki branding and fallback icons; actual
game artwork is optional and remains in the local generated pack.

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

# Include authentic portraits and dialogue relationship icons in the local wiki.
& .\.venv\Scripts\python.exe .\export_guide.py --scope all --include-media

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
| `data\dialogues\<id>.json` | Nodes, connections, choices, effects, requirements, source IDs, and effect/route analysis |
| `data\search\dialogues.json` | Lazy spoken-content index with real node IDs and pronoun variants |
| `data\media\manifest.json` | Optional portrait bindings, relationship symbols, and UI sprite metadata |
| `schemas\*.schema.json` | Version 2 JSON Schema contracts |

IDs are strings, including 64-bit Articy identifiers. Connections retain pin
identity, ordering, and whether the destination is an input or output pin.
Quest-dialogue links come from objective selectors and authored containment, not
similar names or a guessed NPC association. Objectives without conversations
remain objectives; authoring order is not presented as guaranteed progression.
Dialogue `speakerId` values are canonical Articy technical names, allowing
portrait lookup without guessing from display names. Relationship badges show
changes on the explored route, never saved relationship scores.

Quest numbers are assigned when quests start in a playthrough, not fixed global
quest IDs. The wiki deliberately uses stable authored IDs and does not import
save-specific numbering.

The current installed build contains **113 authored quests and 1,569 dialogue
containers**: ordinary, phone, background, repeating, and spot-dependent
conversations. These are asset counts, not a claim that every record is reachable
in gameplay. English localization is preferred. Missing localized lines use
authored English with an explicit warning and `textSource`.

Static exploration is **not save-state simulation**. Conditions and unsupported
template behavior are visible but not evaluated. Literal assignments and
increments are interpreted; other scripts remain unparsed. Recipe selectors are
resolved to real recipe assets. Demeanor describes NPC expressions, not meters.
Sets and increments compose in source order: setting a score to 3 then adding 2
means a final value of 5, whereas adding 2 before setting it means 3. Signed
relationship changes retain negative values even when the game HUD hides them.
Requirements get readable descriptions with the original rules retained.
Internal instructions describe counters, flags and meeting records; their broader
gameplay significance is not inferred from variable names.

`effectAnalysis` merges equivalent accumulated-effect states instead of enumerating
every wording route. Each combination retains ordered effects, requirement
evidence and a connected witness containing real node/edge/reply IDs. Summary
ranges are not a promise that separate maximum gains can be collected together.
Completeness describes structural coverage, not eligibility in a save. Unknown
actions, external boundaries, unfinished paths, accumulating loops and resource
limits remain explicit; repeated-loop outcomes are not presented as maxima.
The default budget is 8,192 states, with separate transition, effect and witness
budgets. Unsupported scripts/actions are retained, never executed.

Complete route lists are bounded to 128 paths / 1,024 nodes per path; cyclic,
oversized, or unfinished graphs remain available with a clear analysis status.
They remain in Research notes independently of the effect-focused summary.
External dialogue links do not automatically execute another conversation.

## GitHub Pages

Site URL: https://jinu-jang.github.io/NivalisNightsWiki/

The app uses relative URLs, so it supports a repository Pages subpath without a
backend, bundler, or CDN. Choose **GitHub Actions** under the repository's Pages
settings and manually run **Publish guide frontend**.
Changes to `web`, `published-data`, or the Pages workflow on `main` also deploy
automatically.

The deployment combines `web` with the reference pack in `published-data`.
Without that pack, it publishes the source-only empty-library onboarding.
The footer and About section credit developer ION LANDS and publisher 505 Games,
identify the wiki as unofficial and not endorsed, and attribute game content to
its respective rights holders. This attribution is not a license or a legal
determination of fair use.

The published reference pack is selected separately from the ignored local output.
The extractor does not publish anything automatically. Game binaries, complete
Unity asset bundles, save files, and local research artifacts must not be included.
Buying the game does not establish redistribution rights; attribution alone does
not establish fair use. Evaluate the rights to any game content before publication.

To update the selected reference pack, regenerate the local wiki and copy only
`output\data` into `published-data`, with `catalog.json` at that folder's root.
Review the changes before committing. Keep `web\data\catalog.json` empty so the
frontend can still be used independently of the published pack.

## Checks

```powershell
& .\.venv\Scripts\python.exe -m unittest discover -s tests
```

Core checks use synthetic data. Local integration checks additionally inspect
the generated pack when present, including Nola's observed route, shared Beer
reward, quest link, source identifiers, and catalog/graph integrity.
