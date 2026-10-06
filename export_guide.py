from __future__ import annotations

import argparse
from collections import defaultdict
import hashlib
import json
from pathlib import Path
import re
import shutil
import tempfile

from extract_dialogue import (
    ArticyAssets, DEFAULT_DIALOGUE, DEFAULT_GAME, DIALOGUE_CLASSES,
    LocalizationIndex, extract_graph, load_english_localization, lossless,
    metadata_enum, parse_effects, script_text,
)


ROOT = Path(__file__).resolve().parent
DEFAULT_QUEST = "Noodle_Bar"
PACK_MARKER = ".nivalis-generated"


def filename(identifier):
    if re.fullmatch(r"[A-Za-z0-9_-]+", identifier):
        return identifier + ".json"
    return hashlib.sha256(identifier.encode("utf-8")).hexdigest()[:24] + ".json"


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n",
                    encoding="utf-8")


def localized_metadata(locale, record, objective=False):
    translation = locale.records.get(record["technicalName"], {})
    title_keys = ("text",) if objective else ("name", "Name")
    title = next((translation[key] for key in title_keys if translation.get(key)), None)
    description = translation.get("description") or translation.get("Description")
    return (
        title or record.get("mDisplayName") or record["technicalName"],
        description or record.get("mText", ""),
    )


def collect_quest(assets, locale, quest_id):
    root = assets.read(quest_id)
    title, description = localized_metadata(locale, root)
    settings = root["mTemplate"]["value"]["mObjectivesDisplay"]["value"]
    owner_id = settings["mOwner"]["objectRef"]
    owner = assets.read(owner_id)["mDisplayName"] if owner_id else None
    records = {}

    def collect(identifier):
        if identifier in records:
            raise ValueError(f"Repeated child in quest hierarchy: {identifier}")
        record = assets.read(identifier)
        records[identifier] = record
        if assets.class_name(identifier) in {"FlowFragment", "SplitObjectives"}:
            for child in record["childrenIds"]:
                collect(child)

    for child in root["childrenIds"]:
        collect(child)
    objectives = []
    contained_dialogues = set()
    flow_nodes = []
    for order, (identifier, record) in enumerate(records.items()):
        class_name = assets.class_name(identifier)
        template = record.get("mTemplate", {}).get("value", {})
        objective_base = template.get("mObjectiveBase", {}).get("value")
        objective_title, objective_description = localized_metadata(locale, record, objective=True)
        flow_nodes.append({
            "id": record["technicalName"], "type": class_name, "title": objective_title,
            "source": assets.source(identifier),
        })
        if class_name in DIALOGUE_CLASSES:
            contained_dialogues.add(record["technicalName"])
        if objective_base is None:
            continue
        target_id = objective_base["mTargetCharacterOrSpot"]["objectRef"]
        target = assets.read(target_id) if target_id else None
        selector = template.get("mDialogueSelector", {}).get("value", {})
        dialogue_ref = selector.get("mTargetDialogue", {}).get("objectRef")
        dialogue_ids = [assets.read(dialogue_ref)["technicalName"]] if dialogue_ref else []
        if dialogue_ref and assets.class_name(dialogue_ref) not in DIALOGUE_CLASSES:
            raise ValueError(f"Objective points to a non-dialogue asset: {record['technicalName']}")
        condition = script_text(
            template.get("mConditionScript", {}).get("value", {}).get("mCondition", {})
        )
        objectives.append({
            "id": record["technicalName"], "title": objective_title,
            "description": objective_description, "type": class_name,
            "targetName": target.get("mDisplayName") if target else None,
            "dialogueIds": dialogue_ids, "completeAtDialogueEnd": bool(selector.get("mCompleteAtEnd")),
            "onStart": parse_effects(script_text(objective_base["mOnStart"])),
            "onComplete": parse_effects(script_text(objective_base["mOnComplete"])),
            "condition": condition, "countVariable": objective_base["mCountVariableName"],
            "targetCount": objective_base["mTargetCount"], "sourceOrder": order,
            "source": assets.source(identifier), "template": lossless(template),
        })
    entry = root["technicalName"] + "::entry"
    exit_id = root["technicalName"] + "::exit"
    flow_nodes.extend([
        {"id": entry, "type": "entry", "title": title},
        {"id": exit_id, "type": "exit", "title": title},
    ])
    flow_edges = []
    flow_ids = {node["id"] for node in flow_nodes}

    def connections(source, pins, is_entry=False):
        for pin in pins:
            for connection in pin["mConnections"]["value"]:
                target_id = connection["mTarget"]["objectRef"]
                target_record = assets.read(target_id)
                target = exit_id if target_id == quest_id else target_record["technicalName"]
                if target not in flow_ids:
                    flow_nodes.append({
                        "id": target, "type": "external",
                        "title": target_record.get("mDisplayName", target),
                    })
                    flow_ids.add(target)
                target_pins = target_record.get("mInputPins", {}).get("value", [])
                input_pin = next((item for item in target_pins
                                  if item["id"] == connection["mTargetPin"]), None)
                condition = script_text(input_pin["mText"]) if input_pin else ""
                entry_condition = script_text(pin["mText"]) if is_entry else ""
                flow_edges.append({
                    "from": source, "to": target, "fromPin": str(pin["id"]),
                    "toPin": str(connection["mTargetPin"]),
                    "conditions": [value for value in [entry_condition, condition] if value],
                    "effects": [] if is_entry else parse_effects(script_text(pin["mText"])),
                })

    connections(entry, root["mInputPins"]["value"], is_entry=True)
    for record in records.values():
        connections(record["technicalName"], record.get("mOutputPins", {}).get("value", []))
    connections(exit_id, root["mOutputPins"]["value"])
    dialogue_ids = sorted(contained_dialogues | {
        dialogue_id for objective in objectives for dialogue_id in objective["dialogueIds"]
    })
    return {
        "schemaVersion": 2, "id": root["technicalName"], "title": title,
        "description": description, "owner": owner, "objectives": objectives,
        "dialogueIds": dialogue_ids, "onStart": parse_effects(script_text(settings["mOnStart"])),
        "onComplete": parse_effects(script_text(settings["mOnComplete"])),
        "flow": {"entry": entry, "exit": exit_id, "nodes": flow_nodes, "edges": flow_edges},
        "warnings": ["Objective source order is authoring order, not a guaranteed gameplay sequence."],
        "source": assets.source(quest_id),
    }


def quest_links(quests):
    dialogue_quests = defaultdict(set)
    dialogue_objectives = defaultdict(set)
    for quest in quests:
        for dialogue_id in quest["dialogueIds"]:
            dialogue_quests[dialogue_id].add(quest["id"])
        for objective in quest["objectives"]:
            for dialogue_id in objective["dialogueIds"]:
                dialogue_objectives[dialogue_id].add(objective["id"])
    return dialogue_quests, dialogue_objectives


def validate_pack(data_dir):
    catalog = json.loads((data_dir / "catalog.json").read_text(encoding="utf-8"))
    if catalog["schemaVersion"] != 2:
        raise ValueError("Unsupported guide catalog version")
    quest_ids = {item["id"] for item in catalog["quests"]}
    dialogue_ids = {item["id"] for item in catalog["dialogues"]}
    if len(quest_ids) != len(catalog["quests"]) or len(dialogue_ids) != len(catalog["dialogues"]):
        raise ValueError("Duplicate catalog identifiers")

    def document_path(item):
        path = (data_dir / item["file"]).resolve()
        if not path.is_relative_to(data_dir.resolve()):
            raise ValueError(f"Catalog path escapes the data pack: {item['file']}")
        return path

    objective_ids = set()
    for item in catalog["quests"]:
        document = json.loads(document_path(item).read_text(encoding="utf-8"))
        objective_ids.update(objective["id"] for objective in document["objectives"])
    for kind, items in (("quest", catalog["quests"]), ("dialogue", catalog["dialogues"])):
        for item in items:
            document = json.loads(document_path(item).read_text(encoding="utf-8"))
            if document["schemaVersion"] != 2:
                raise ValueError(f"Unsupported {kind} document version")
            if kind == "quest":
                if document["id"] != item["id"]:
                    raise ValueError("Quest catalog/document ID mismatch")
                graph = document["flow"]
                if catalog.get("coverage", {}).get("scope") == "all" and not set(
                    document["dialogueIds"]
                ) <= dialogue_ids:
                    raise ValueError(f"Missing exported quest dialogue: {item['id']}")
            else:
                if document["dialogueId"] != item["id"]:
                    raise ValueError("Dialogue catalog/document ID mismatch")
                graph = document
                if not set(document["questIds"]) <= quest_ids:
                    raise ValueError(f"Unknown quest reference in {item['id']}")
                if not set(document["objectiveIds"]) <= objective_ids:
                    raise ValueError(f"Unknown objective reference in {item['id']}")
            node_ids = {node["id"] for node in graph["nodes"]}
            if len(node_ids) != len(graph["nodes"]):
                raise ValueError(f"Duplicate graph node: {item['id']}")
            if graph["entry"] not in node_ids or graph["exit"] not in node_ids:
                raise ValueError(f"Missing graph boundary: {item['id']}")
            for edge in graph["edges"]:
                if edge["from"] not in node_ids or edge["to"] not in node_ids:
                    raise ValueError(f"Dangling graph edge in {item['id']}")
    if catalog["defaultQuestId"] and catalog["defaultQuestId"] not in quest_ids:
        raise ValueError("Default quest is not in the catalog")
    if catalog["defaultDialogueId"] and catalog["defaultDialogueId"] not in dialogue_ids:
        raise ValueError("Default dialogue is not in the catalog")
    return catalog


def export_pack(assets, locale, data_dir, scope="quest", quest_name=DEFAULT_QUEST,
                dialogue_name=DEFAULT_DIALOGUE, include_raw=False):
    quests = [
        collect_quest(assets, locale, identifier)
        for identifier, class_name in assets.classes.items() if class_name == "QuestFlow"
    ]
    all_dialogues = {
        assets.read(identifier)["technicalName"]: identifier
        for identifier, class_name in assets.classes.items() if class_name in DIALOGUE_CLASSES
    }
    if scope == "quest":
        selected_quests = [quest for quest in quests if quest["id"] == quest_name]
        if not selected_quests:
            raise ValueError(f"Quest not found: {quest_name}")
        selected_dialogues = {
            identifier for quest in selected_quests for identifier in quest["dialogueIds"]
        }
    elif scope == "dialogue":
        if dialogue_name not in all_dialogues:
            raise ValueError(f"Dialogue not found: {dialogue_name}")
        selected_dialogues = {dialogue_name}
        selected_quests = [quest for quest in quests if dialogue_name in quest["dialogueIds"]]
    elif scope == "all":
        selected_quests = quests
        selected_dialogues = set(all_dialogues)
    else:
        raise ValueError(f"Unknown export scope: {scope}")
    dialogue_quests, dialogue_objectives = quest_links(selected_quests)
    demeanor_names = metadata_enum(
        assets.data_dir / "il2cpp_data" / "Metadata" / "global-metadata.dat",
        "Articy.Nivalis", "DemeanorFeature",
    )
    quest_catalog = []
    for quest in sorted(selected_quests, key=lambda item: item["title"].casefold()):
        file = "quests/" + filename(quest["id"])
        write_json(data_dir / Path("quests") / filename(quest["id"]), quest)
        quest_catalog.append({
            "id": quest["id"], "title": quest["title"], "description": quest["description"],
            "owner": quest["owner"], "file": file, "objectiveCount": len(quest["objectives"]),
            "dialogueCount": len(set(quest["dialogueIds"]) & selected_dialogues),
        })
    dialogue_catalog = []
    analysis_counts = defaultdict(int)
    for index, name in enumerate(sorted(selected_dialogues), 1):
        graph = extract_graph(assets, name, locale, demeanor_names, include_raw=include_raw)
        graph["questIds"] = sorted(dialogue_quests[name])
        graph["objectiveIds"] = sorted(dialogue_objectives[name])
        for node in graph["nodes"]:
            if node["type"] == "external" and node["id"] in all_dialogues:
                node["dialogueId"] = node["id"]
        file = "dialogues/" + filename(name)
        write_json(data_dir / Path("dialogues") / filename(name), graph)
        dialogue_catalog.append({
            "id": name, "title": graph["title"],
            "speakers": sorted({node["speaker"] for node in graph["nodes"]
                                if node["type"] == "line" and node["speaker"]}),
            "file": file, "questIds": graph["questIds"], "objectiveIds": graph["objectiveIds"],
            "nodeCount": len(graph["nodes"]), "warningCount": len(graph["warnings"]),
        })
        analysis_counts[graph["analysis"]["status"]] += 1
        if index % 100 == 0:
            print(f"Exported {index}/{len(selected_dialogues)} dialogues", flush=True)
    default_quest = (
        DEFAULT_QUEST if any(quest["id"] == DEFAULT_QUEST for quest in selected_quests)
        else selected_quests[0]["id"] if selected_quests else None
    )
    default_dialogue = (
        DEFAULT_DIALOGUE if DEFAULT_DIALOGUE in selected_dialogues
        else next(iter(sorted(selected_dialogues)), None)
    )
    catalog = {
        "schemaVersion": 2, "game": {"title": "Nivalis Nights", "unityVersion":
            next(iter(assets.objects.values())).assets_file.unity_version},
        "defaultQuestId": default_quest, "defaultDialogueId": default_dialogue,
        "quests": quest_catalog, "dialogues": dialogue_catalog,
        "coverage": {
            "questsFound": len(quests), "questsExported": len(selected_quests),
            "dialoguesFound": len(all_dialogues), "dialoguesExported": len(selected_dialogues),
            "routeAnalysis": dict(analysis_counts), "scope": scope,
        },
    }
    write_json(data_dir / "catalog.json", catalog)
    (data_dir / PACK_MARKER).write_text("Generated locally; not approved for redistribution.\n", encoding="utf-8")
    validate_pack(data_dir)
    return catalog


def install_site(data_dir, output_dir):
    if output_dir.resolve() in {ROOT, (ROOT / "web").resolve()}:
        raise ValueError("Use a separate output directory, not repository/web source")
    target = output_dir / "data"
    if target.exists() and not (target / PACK_MARKER).exists():
        raise ValueError(f"Refusing to replace unmarked data directory: {target}")
    output_dir.mkdir(parents=True, exist_ok=True)
    for path in (ROOT / "web").iterdir():
        if path.name == "data":
            continue
        destination = output_dir / path.name
        if path.is_dir():
            shutil.copytree(path, destination, dirs_exist_ok=True)
        else:
            shutil.copy2(path, destination)
    if target.exists():
        shutil.rmtree(target)
    shutil.move(str(data_dir), str(target))


def main():
    parser = argparse.ArgumentParser(description="Build a static Nivalis quest/dialogue guide from local assets.")
    parser.add_argument("--game-dir", type=Path, default=DEFAULT_GAME)
    parser.add_argument("--scope", choices=("quest", "dialogue", "all"))
    parser.add_argument("--quest-id", default=DEFAULT_QUEST)
    parser.add_argument("--dialogue-id")
    parser.add_argument("--output-dir", type=Path, default=Path("output"))
    parser.add_argument("--include-raw", action="store_true")
    args = parser.parse_args()
    scope = args.scope or ("dialogue" if args.dialogue_id else "quest")
    assets = ArticyAssets(args.game_dir)
    locale = LocalizationIndex(load_english_localization(assets.data_dir))
    args.output_dir.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".nivalis-export-", dir=args.output_dir.parent) as stage:
        data_dir = Path(stage) / "data"
        catalog = export_pack(assets, locale, data_dir, scope, args.quest_id,
                              args.dialogue_id or DEFAULT_DIALOGUE, args.include_raw)
        install_site(data_dir, args.output_dir)
    print(f"{catalog['coverage']['questsExported']} quests / "
          f"{catalog['coverage']['dialoguesExported']} dialogues")
    print(f"Static site: {args.output_dir.resolve()}")


if __name__ == "__main__":
    main()
