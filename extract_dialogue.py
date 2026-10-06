from __future__ import annotations

from collections import defaultdict
import json
from pathlib import Path
import re
import struct

import UnityPy
from UnityPy.helpers.TypeTreeGenerator import TypeTreeGenerator


DEFAULT_GAME = Path(r"C:\Program Files (x86)\Steam\steamapps\common\Nivalis Nights")
DEFAULT_DIALOGUE = "Dlg_2FF9F477"
DIALOGUE_CLASSES = {
    "Dialogue", "PhoneDialogue", "NPCBackgroundConv", "Repeating_Dialogue", "StayOnSpotOrNot",
}
ASSIGNMENT = re.compile(
    r"([A-Za-z_]\w*(?:\.[A-Za-z_]\w*)+)\s*(\+=|-=|=)\s*(true|false|-?\d+)"
)


class LocalTypeTreeGenerator(TypeTreeGenerator):
    def load_local_game(self, root_dir):
        super().load_local_game(root_dir)
        self.assemblies = set(self.get_loaded_dll_names())

    def get_nodes(self, assembly, fullname):
        # IL2CPP's assembly names omit .dll; Unity's MonoScript references include it.
        if assembly not in self.assemblies:
            assembly = assembly.removesuffix(".dll")
        nodes = super().get_nodes(assembly, fullname)
        # AssetsTools omits padding after Unity's one-byte enabled flag.
        for node in nodes:
            if node.m_Level == 1 and node.m_Name == "m_Enabled":
                node.m_MetaFlag |= 0x4000
        return nodes


def lossless(value):
    if isinstance(value, dict):
        return {key: lossless(item) for key, item in value.items()}
    if isinstance(value, list):
        return [lossless(item) for item in value]
    if isinstance(value, int) and not isinstance(value, bool) and abs(value) > 2**53 - 1:
        return str(value)
    return value


def script_text(value):
    return value.get("value", {}).get("mRawScript", "")


def parse_effects(script):
    uncommented = re.sub(r"(?m)^\s*//[^\n]*$", "", script)
    effects = []
    for statement in uncommented.split(";"):
        statement = statement.strip()
        if not statement:
            continue
        match = ASSIGNMENT.fullmatch(statement)
        if not match:
            effects.append({"type": "unparsed", "script": statement})
            continue
        variable, operator, literal = match.groups()
        value = {"true": True, "false": False}.get(literal)
        if literal not in ("true", "false"):
            value = int(literal)
        if operator != "=" and isinstance(value, bool):
            effects.append({"type": "unparsed", "script": statement})
        elif operator == "=":
            effects.append({"type": "set", "variable": variable, "value": value})
        else:
            delta = value if operator == "+=" else -value
            effects.append({"type": "delta", "variable": variable, "value": delta})
    return effects


def summarize_effects(effects):
    deltas = defaultdict(int)
    assignments = {}
    unlocks = []
    unparsed = []
    actions = []
    for effect in effects:
        kind = effect["type"]
        if kind == "delta":
            deltas[effect["variable"]] += effect["value"]
        elif kind == "set":
            assignments[effect["variable"]] = effect["value"]
        elif kind == "unlock_recipe":
            if effect["recipe"] not in unlocks:
                unlocks.append(effect["recipe"])
        elif kind == "unparsed":
            unparsed.append(effect["script"])
        elif kind == "template_action":
            actions.append(effect["label"])
        else:
            raise ValueError(f"Unknown effect type: {kind}")
    return {
        "deltas": dict(deltas),
        "assignments": assignments,
        "recipes": unlocks,
        "unparsed": unparsed,
        "actions": actions,
    }


def template_action(assets, class_name, template):
    label = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", class_name).replace("_", " ")
    result = {"type": "template_action", "action": class_name,
              "label": label, "template": lossless(template)}
    if class_name in {"GivePlayerItemTmpl", "TakePlayerItemTmpl"}:
        selector = template["mItemSelector"]["value"]
        item_id = selector["mItem"]["objectRef"]
        item = assets.read(item_id)
        verb = "Receive" if class_name == "GivePlayerItemTmpl" else "Give"
        result.update({
            "label": f"{verb} {selector['mAmount']:g} x {item['mDisplayName']}",
            "referenceId": item["technicalName"], "referenceKind": "item",
            "quantity": selector["mAmount"],
        })
    elif class_name in {"StartQuestTmpl", "EndQuestTmpl"}:
        quest_id = template["mQuestSelector"]["value"]["mQuest"]["objectRef"]
        quest = assets.read(quest_id)
        verb = "Start quest" if class_name == "StartQuestTmpl" else "End quest"
        result.update({
            "label": f"{verb}: {quest['mDisplayName']}",
            "referenceId": quest["technicalName"], "referenceKind": "quest",
        })
        if class_name == "EndQuestTmpl":
            result["stateValue"] = template["mQuestStateSelector"]["value"]["mEnumValue"]
    elif class_name == "UnlockSteamAchievement":
        result["label"] = "Steam achievement: " + template["mName"]["value"]["mText"]
    return result


def metadata_enum(path, namespace, name):
    data = path.read_bytes()
    magic, version = struct.unpack_from("<II", data)
    if magic != 0xFAB11BAF or version != 27:
        raise ValueError(f"Unsupported IL2CPP metadata: magic={magic:#x}, version={version}")
    tables = [struct.unpack_from("<II", data, 8 + index * 8) for index in range(20)]
    strings, string_size = tables[2]
    fields, field_size = tables[11]
    types, type_size = tables[19]
    if type_size % 88 or field_size % 12:
        raise ValueError("Unexpected IL2CPP v27 type/field table layout")

    def text(index):
        if not 0 <= index < string_size:
            raise ValueError(f"Invalid metadata string index: {index}")
        start = strings + index
        end = data.index(b"\0", start, strings + string_size)
        return data[start:end].decode("utf-8")

    defaults_offset, defaults_size = tables[7]
    defaults = {
        field: offset
        for field, _, offset in struct.iter_unpack(
            "<iii", data[defaults_offset : defaults_offset + defaults_size]
        )
    }
    for offset in range(types, types + type_size, 88):
        name_index, namespace_index = struct.unpack_from("<II", data, offset)
        if text(name_index) != name or text(namespace_index) != namespace:
            continue
        bitfield = struct.unpack_from("<I", data, offset + 80)[0]
        if not bitfield & 2:
            raise ValueError(f"{namespace}.{name} is not an enum")
        field_start = struct.unpack_from("<i", data, offset + 32)[0]
        field_count = struct.unpack_from("<H", data, offset + 68)[0]
        result = {}
        for field in range(field_start, field_start + field_count):
            name_index = struct.unpack_from("<I", data, fields + field * 12)[0]
            default = defaults.get(field, -1)
            if default >= 0:
                value = struct.unpack_from("<i", data, tables[8][0] + default)[0]
                result[value] = text(name_index)
        return result
    raise ValueError(f"Enum not found: {namespace}.{name}")


class ArticyAssets:
    def __init__(self, game_dir):
        self.game_dir = game_dir
        self.data_dir = game_dir / "Nivalis Nights_Data"
        self.env = UnityPy.load(str(self.data_dir / "resources.assets"))
        self.objects = {}
        self.names = {}
        self.scripts = {}
        self.classes = {}
        self.records = {}
        for obj in list(self.env.objects):
            if obj.type.name != "MonoBehaviour":
                continue
            head = obj.parse_monobehaviour_head()
            key = (head.m_Script.m_FileID, head.m_Script.m_PathID)
            if key not in self.scripts:
                self.scripts[key] = head.m_Script.read()
            script = self.scripts[key]
            if script.m_Namespace != "Articy.Nivalis":
                continue
            raw = obj.get_raw_data()
            offset = 32 + ((len(head.m_Name.encode("utf-8")) + 3) & ~3)
            identifier, length = struct.unpack_from(obj.reader.endian + "Qi", raw, offset)
            if length < 0 or offset + 12 + length > len(raw):
                raise ValueError(f"Invalid technical name for Articy asset: {head.m_Name}")
            name = raw[offset + 12 : offset + 12 + length].decode("utf-8")
            if identifier in self.objects or name in self.names:
                raise ValueError(f"Duplicate Articy asset: {name}")
            self.objects[identifier] = obj
            self.names[name] = identifier
            self.classes[identifier] = script.m_ClassName
        if not self.objects:
            raise ValueError("No Articy records found in resources.assets")
        self.generator = LocalTypeTreeGenerator(
            next(iter(self.objects.values())).assets_file.unity_version
        )
        self.generator.load_local_game(str(game_dir))
        self.env.typetree_generator = self.generator

    def read(self, identifier):
        if identifier not in self.records:
            obj = self.objects[identifier]
            record = obj.read_typetree()
            if record["id"] != identifier:
                raise ValueError(f"Articy identifier mismatch for {record['m_Name']}")
            self.records[identifier] = record
        return self.records[identifier]

    def class_name(self, identifier):
        return self.classes[identifier]

    def source(self, identifier):
        obj = self.objects[identifier]
        return {
            "file": "resources.assets",
            "pathId": str(obj.path_id),
            "byteOffset": obj.byte_start,
            "byteLength": obj.byte_size,
        }


def find_localized_dialogue(value, required_guids):
    matches = []

    def visit(item):
        if isinstance(item, dict):
            lines = item.get("lines")
            if isinstance(lines, list):
                guids = {line.get("GUID") for line in lines}
                if required_guids <= guids:
                    matches.append(item)
            for child in item.values():
                visit(child)
        elif isinstance(item, list):
            for child in item:
                visit(child)

    visit(value)
    if len(matches) != 1:
        raise ValueError(f"Expected one matching localized dialogue, found {len(matches)}")
    return matches[0]


def load_english_localization(data_dir):
    path = data_dir / "sharedassets0.assets"
    env = UnityPy.Environment()
    with path.open("rb") as stream:
        env.load_file(stream, name=str(path))
        for obj in env.objects:
            if obj.type.name != "TextAsset":
                continue
            obj.reset()
            if obj.reader.read_aligned_string() != "LocFile_En":
                continue
            asset = obj.read()
            return {
                "source": {"file": path.name, "pathId": str(obj.path_id), "name": asset.m_Name},
                "raw": json.loads(asset.m_Script),
            }
    raise ValueError("English localization TextAsset LocFile_En was not found")


class LocalizationIndex:
    def __init__(self, localization):
        self.source = localization["source"]
        self.records = {}
        self.lines = {}
        self.choices = {}
        self.dialogues = []

        def insert(mapping, identifier, value):
            if identifier in mapping and mapping[identifier] != value:
                raise ValueError(f"Conflicting English localization for {identifier}")
            mapping[identifier] = value

        def visit(value):
            if isinstance(value, dict):
                if value.get("GUID") and "TextWPronouns" in value:
                    insert(self.lines, value["GUID"], value)
                elif value.get("GUID"):
                    insert(self.records, value["GUID"], value)
                if value.get("ArticyGUID") and "choiceText" in value:
                    insert(self.choices, value["ArticyGUID"], value["choiceText"])
                if isinstance(value.get("lines"), list):
                    self.dialogues.append(value)
                for child in value.values():
                    visit(child)
            elif isinstance(value, list):
                for child in value:
                    visit(child)

        visit(localization["raw"])

    def select(self, required_guids, strict=True):
        missing = required_guids - self.lines.keys()
        if missing and strict:
            raise ValueError(f"Missing localized dialogue lines: {sorted(missing)}")
        return {
            "source": self.source,
            "raw": {"lines": [self.lines[identifier] for identifier in sorted(required_guids - missing)]},
            "missingGuids": sorted(missing),
        }


def load_localization(data_dir, required_guids):
    localization = load_english_localization(data_dir)
    return {
        "source": localization["source"],
        "raw": find_localized_dialogue(localization["raw"], required_guids),
    }


class RouteEnumerationError(ValueError):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status


def enumerate_paths(graph, max_paths=128, max_steps=1024):
    nodes = {node["id"]: node for node in graph["nodes"]}
    outgoing = defaultdict(list)
    for edge in graph["edges"]:
        outgoing[edge["from"]].append(edge)
    paths = []

    stack = [(graph["entry"], [], [], [], [])]
    while stack:
        identifier, visited, choices, effects, conditions = stack.pop()
        if identifier in visited:
            raise RouteEnumerationError("cycle", f"Cycle encountered at {identifier}; finite route enumeration stopped")
        if len(visited) >= max_steps:
            raise RouteEnumerationError("limit", f"Route exceeded the {max_steps}-node analysis limit")
        node = nodes[identifier]
        effects = effects + node["effects"]
        conditions = conditions + node["conditions"]
        if node["type"] == "choice":
            choices = choices + [identifier]
        if identifier == graph["exit"]:
            paths.append({
                "choices": choices,
                "nodes": visited + [identifier],
                "effects": effects,
                "conditions": conditions,
                "summary": summarize_effects(effects),
            })
            if len(paths) > max_paths:
                raise RouteEnumerationError("limit", f"More than {max_paths} routes; use the graph instead")
            continue
        if not outgoing[identifier]:
            raise RouteEnumerationError("dead_end", f"Unexpected dead end before conversation exit: {identifier}")
        for edge in reversed(outgoing[identifier]):
            stack.append((
                edge["to"], visited + [identifier], choices,
                effects + edge["effects"], conditions + edge["conditions"],
            ))
    return paths


def extract_graph(assets, dialogue_name, localization_index=None, demeanor_names=None,
                  include_raw=True):
    root_id = assets.names[dialogue_name]
    root = assets.read(root_id)
    if assets.class_name(root_id) not in DIALOGUE_CLASSES:
        raise ValueError(f"{dialogue_name} is not a Dialogue asset")
    records = {identifier: assets.read(identifier) for identifier in root["childrenIds"]}
    line_guids = {
        record["technicalName"] for identifier, record in records.items()
        if record.get("mSpeaker", {}).get("objectRef") and "MainCharacter" not in assets.class_name(
            record["mSpeaker"]["objectRef"]
        )
    }
    localization = (
        localization_index.select(line_guids, strict=False) if localization_index
        else load_localization(assets.data_dir, line_guids)
    )
    localized = {
        line["GUID"]: line for line in localization["raw"]["lines"] if line["GUID"]
    }
    choice_texts = localization_index.choices if localization_index else {
        choice["ArticyGUID"]: choice["choiceText"]
        for line in localization["raw"]["lines"]
        for choice in line["ChoicesWPronouns"] if choice["ArticyGUID"]
    }
    demeanor_names = demeanor_names if demeanor_names is not None else metadata_enum(
        assets.data_dir / "il2cpp_data" / "Metadata" / "global-metadata.dat",
        "Articy.Nivalis", "DemeanorFeature",
    )
    entry, exit_id = dialogue_name + "::entry", dialogue_name + "::exit"
    nodes = []
    warnings = []
    name_by_id = {identifier: record["technicalName"] for identifier, record in records.items()}
    for identifier, record in records.items():
        class_name = assets.class_name(identifier)
        speaker_id = record.get("mSpeaker", {}).get("objectRef")
        speaker = assets.read(speaker_id)["mDisplayName"] if speaker_id else None
        is_player = bool(speaker_id and "MainCharacter" in assets.class_name(speaker_id))
        kind = "choice" if is_player else "line" if speaker_id else (
            "condition" if class_name == "Condition" else
            "subdialogue" if class_name in DIALOGUE_CLASSES else "action"
        )
        expression = script_text(record.get("mExpression", {}))
        conditions = [expression] if class_name == "Condition" and expression else []
        effects = [] if class_name == "Condition" else parse_effects(expression)
        template = record.get("mTemplate", {}).get("value", {})
        if class_name == "UnlockRecipe":
            recipe_id = template["mRecipeSelector"]["value"]["mRecipe"]["objectRef"]
            recipe = assets.read(recipe_id)
            effects.append({
                "type": "unlock_recipe", "recipe": recipe["mDisplayName"],
                "articyId": str(recipe_id), "source": assets.source(recipe_id),
            })
        elif kind == "action" and template:
            effects.append(template_action(assets, class_name, template))
        pronouns = choice_texts.get(record["technicalName"]) if is_player else (
            localized.get(record["technicalName"], {}).get("TextWPronouns")
        )
        localized_text = pronouns.get("TextHeVersion") if pronouns else None
        text = localized_text if localized_text is not None else record.get("mText", "")
        if speaker_id and localized_text is None:
            warnings.append(
                f"{record['technicalName']}: English localization missing; displaying authored asset text"
            )
        demeanor = template.get("mDemeanorFeature", {}).get("value", {}).get("mDemeanorFeature")
        nodes.append({
            "id": record["technicalName"], "articyId": str(identifier),
            "type": kind, "className": class_name, "speaker": speaker, "text": text,
            "textSource": "localization" if localized_text is not None else "authored",
            "pronounText": pronouns, "effects": effects, "conditions": conditions,
            "script": expression,
            "demeanor": None if demeanor is None else {
                "value": demeanor, "name": demeanor_names.get(demeanor, "Unspecified"),
            },
            "position": record["mPosition"], "source": assets.source(identifier),
            "raw": lossless(record),
        })
    for identifier, kind in ((entry, "entry"), (exit_id, "exit")):
        nodes.append({
            "id": identifier, "type": kind, "text": root["mDisplayName"],
            "speaker": None, "effects": [], "conditions": [],
        })
    edges = []

    def add_connections(source, pins, pin_kind):
        for pin_order, pin in enumerate(pins):
            script = script_text(pin["mText"])
            for order, connection in enumerate(pin["mConnections"]["value"]):
                target_id = connection["mTarget"]["objectRef"]
                target_pin = connection["mTargetPin"]
                if target_id == root_id:
                    if target_pin in {item["id"] for item in root["mOutputPins"]["value"]}:
                        target = exit_id
                    elif target_pin in {item["id"] for item in root["mInputPins"]["value"]}:
                        target = entry
                    else:
                        raise ValueError(f"Unknown conversation boundary pin: {target_pin}")
                    target_record = root
                else:
                    target_record = assets.read(target_id)
                    target = name_by_id.get(target_id, target_record["technicalName"])
                    if target_id not in records and not any(n["id"] == target for n in nodes):
                        nodes.append({
                            "id": target, "type": "external",
                            "text": target_record.get("mDisplayName", target),
                            "speaker": None, "effects": [], "conditions": [],
                            "source": assets.source(target_id), "raw": lossless(target_record),
                        })
                input_pin = next(
                    (item for item in target_record.get("mInputPins", {}).get("value", [])
                     if item["id"] == target_pin), None,
                )
                output_pin = next(
                    (item for item in target_record.get("mOutputPins", {}).get("value", [])
                     if item["id"] == target_pin), None,
                )
                if input_pin is None and output_pin is None:
                    raise ValueError(f"Target pin not found for {target}: {target_pin}")
                condition = script_text(input_pin["mText"]) if input_pin else ""
                edges.append({
                    "id": f"{source}:{pin['id']}:{order}", "from": source, "to": target,
                    "fromPin": str(pin["id"]), "toPin": str(target_pin),
                    "targetPinRole": "input" if input_pin else "output",
                    "pinOrder": pin_order, "order": order, "label": connection["mLabel"],
                    "effects": parse_effects(script) if pin_kind == "output" else [],
                    "script": script,
                    "conditions": ([script] if pin_kind == "input" and script else [])
                    + ([condition] if condition else []),
                })

    add_connections(entry, root["mInputPins"]["value"], "input")
    for identifier, record in records.items():
        add_connections(name_by_id[identifier], record.get("mOutputPins", {}).get("value", []), "output")
        if assets.class_name(identifier) == "Jump":
            target_id = record["mTarget"]["objectRef"]
            target_record = assets.read(target_id)
            target = name_by_id.get(target_id, target_record["technicalName"])
            target_pin = record["mTargetPin"]
            input_pin = next(
                (pin for pin in target_record.get("mInputPins", {}).get("value", [])
                 if pin["id"] == target_pin), None,
            )
            output_pin = next(
                (pin for pin in target_record.get("mOutputPins", {}).get("value", [])
                 if pin["id"] == target_pin), None,
            )
            if input_pin is None and output_pin is None:
                raise ValueError(f"Jump target pin not found: {record['technicalName']}")
            if target_id not in records and not any(node["id"] == target for node in nodes):
                nodes.append({
                    "id": target, "type": "external", "text": target_record.get("mText", target),
                    "speaker": None, "effects": [], "conditions": [],
                    "source": assets.source(target_id), "raw": lossless(target_record),
                })
            edges.append({
                "id": f"{name_by_id[identifier]}:jump", "from": name_by_id[identifier],
                "to": target, "fromPin": None, "toPin": str(target_pin),
                "targetPinRole": "input" if input_pin else "output",
                "pinOrder": 0, "order": 0, "label": "Jump", "effects": [],
                "conditions": [script_text(input_pin["mText"])]
                if input_pin and script_text(input_pin["mText"]) else [],
            })
    add_connections(exit_id, root["mOutputPins"]["value"], "output")
    graph = {
        "schemaVersion": 2, "dialogueId": dialogue_name, "title": root["mDisplayName"],
        "entry": entry, "exit": exit_id, "nodes": nodes, "edges": edges,
        "provenance": {
            "unityVersion": next(iter(assets.objects.values())).assets_file.unity_version,
            "conversation": assets.source(root_id), "rawConversation": lossless(root),
            "localization": lossless(localization), "demeanorEnum": demeanor_names,
        },
        "limitations": [
            "Static asset graph, not a simulation of game code or save state.",
            "Conditions are preserved, not evaluated; route totals are conditional on them.",
            "Only literal assignments and increments are interpreted; other scripts stay unparsed.",
            "Demeanor is an NPC expression, not a relationship-meter change.",
            "Friend/Business icon meanings come from gameplay; Enemy's UI label is unconfirmed.",
            "Routes stop at this conversation's exit; external dialogue gates are shown separately.",
        ],
    }
    try:
        graph["paths"] = enumerate_paths(graph)
        graph["analysis"] = {"status": "complete", "message": "All finite routes inside this dialogue"}
    except RouteEnumerationError as error:
        graph["paths"] = []
        graph["analysis"] = {"status": error.status, "message": str(error)}
        warnings.append(str(error))
    graph["outcomes"] = list({
        json.dumps(path["summary"], sort_keys=True): path["summary"]
        for path in graph["paths"]
    }.values())
    for node in graph["nodes"]:
        if node["type"] == "subdialogue":
            node["dialogueId"] = node["id"]
            warnings.append(f"{node['id']} is a nested dialogue; follow its separate graph")
        for effect in node["effects"]:
            if effect["type"] == "template_action":
                warnings.append(f"{node['id']}: {effect['action']} requires game behavior; not simulated")
    graph["warnings"] = warnings
    if not include_raw:
        for node in graph["nodes"]:
            node.pop("raw", None)
        graph["provenance"].pop("rawConversation")
        graph["provenance"]["localization"].pop("raw")
    return graph


def main():
    from export_guide import main as export_main
    export_main()


if __name__ == "__main__":
    main()
