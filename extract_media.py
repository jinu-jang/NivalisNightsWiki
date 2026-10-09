"""Read-only, pointer-bound dialogue artwork export from a local game install."""

from __future__ import annotations

import argparse
from collections import defaultdict
import json
from pathlib import Path
import shutil
import uuid

from PIL import Image
import UnityPy
from UnityPy.classes.PPtr import PPtr

from extract_dialogue import ArticyAssets, DEFAULT_GAME, LocalTypeTreeGenerator


OUTPUT_ROOT = Path(__file__).resolve().parent / "output"
CHARACTER_CLASSES = {"DefaultSupportingCharacterTemplate", "DefaultMainCharacterTemplate"}
PERSON = ("Nivalis.GhostSystem.Ai", "Person")
AGENT = ("Nivalis.GhostSystem.Ai", "Agent")
CHARACTER = ("Nivalis", "Character")
DIALOGUE_UI = ("Nivalis.Dialogue", "DialogueUI")
MATRIX = ("", "MiniRelationMatrix")
WIDGET = ("", "MiniRelationWidget")
UI_IMAGE = ("UnityEngine.UI", "Image")
JOURNAL_ENTRY = ("Nivalis.UI", "ActiveJournalEntryItem")
JOURNAL_LIST = ("Nivalis.UI", "JournalListItemUI")
CHARACTER_WIDGET = ("Nivalis.UI", "CharacterWindowWidgetCharacter")
VERIFIED_CLASSES = {
    PERSON, AGENT, CHARACTER, DIALOGUE_UI, MATRIX, WIDGET, UI_IMAGE,
    JOURNAL_ENTRY, JOURNAL_LIST, CHARACTER_WIDGET,
}
METERS = {
    "friendly": ("Friend", "friendWidget"),
    "enemy": ("Enemy", "enemyWidget"),
    "romance": ("Romance", "romanceWidget"),
    "business": ("Business", "businessWidget"),
}


def source(obj) -> dict:
    return {
        "file": Path(obj.assets_file.name.replace("\\", "/")).name,
        "pathId": str(obj.path_id),
    }


def object_key(obj):
    return id(obj.assets_file), obj.path_id


def resolve_pointer(owner, value):
    if not value["m_PathID"]:
        return None
    return PPtr(
        m_FileID=value["m_FileID"], m_PathID=value["m_PathID"],
        assetsfile=owner.assets_file,
    ).deref()


class MediaReader:
    def __init__(self):
        self.scripts = {}
        self.records = {}

    def script_class(self, obj):
        head = obj.parse_monobehaviour_head()
        pointer = head.m_Script
        key = (id(obj.assets_file), pointer.m_FileID, pointer.m_PathID)
        if key not in self.scripts:
            script = pointer.deref_parse_as_object()
            self.scripts[key] = script.m_Namespace, script.m_ClassName
        return self.scripts[key]

    def mono(self, obj, expected):
        if expected not in VERIFIED_CLASSES:
            raise ValueError(f"Unverified media class: {expected}")
        if obj.type.name != "MonoBehaviour" or self.script_class(obj) != expected:
            raise ValueError(f"Unexpected component at {source(obj)}; expected {expected}")
        return self._read(obj)

    def native(self, obj, expected):
        if obj.type.name != expected:
            raise ValueError(f"Unexpected native object at {source(obj)}; expected {expected}")
        return self._read(obj)

    def _read(self, obj):
        key = object_key(obj)
        if key not in self.records:
            self.records[key] = obj.read_typetree()
        return self.records[key]


def binding_step(obj, field):
    return {"source": source(obj), "field": field}


def person_portrait(reader, obj, record):
    """Resolve authored precedence, never names or image filenames."""
    via = [binding_step(obj, "overridePortrait")]
    portrait = resolve_pointer(obj, record["overridePortrait"])
    if portrait is not None:
        return portrait, via, None
    via = [binding_step(obj, "CharacterPrefab")]
    agent = resolve_pointer(obj, record["CharacterPrefab"])
    if agent is None:
        return None, via, "missing-character-prefab"
    agent_record = reader.mono(agent, AGENT)
    via.append(binding_step(agent, "character"))
    character = resolve_pointer(agent, agent_record["character"])
    if character is None:
        return None, via, "missing-agent-character"
    character_record = reader.mono(character, CHARACTER)
    via.append(binding_step(character, "portrait"))
    portrait = resolve_pointer(character, character_record["portrait"])
    return portrait, via, None if portrait is not None else "missing-character-portrait"


def collect_characters(assets, objects, reader, sprites):
    characters = {}
    for identifier, class_name in assets.classes.items():
        if class_name not in CHARACTER_CLASSES:
            continue
        record = assets.read(identifier)
        name = record["technicalName"]
        characters[name] = {
            "id": name, "articyId": str(identifier), "name": record["mDisplayName"],
            "className": class_name, "source": assets.source(identifier),
            "portrait": None, "variableName": None, "bindings": [],
        }
    candidates = defaultdict(dict)
    coverage = {
        "canonicalCharacters": len(characters), "personDefinitions": 0,
        "namedPersonBindings": 0, "portraitBindings": 0,
        "charactersWithBindings": 0, "charactersWithPortrait": 0,
        "charactersWithVariableName": 0, "variableConflicts": [],
        "unresolvedCharacters": [], "conflicts": [], "noncanonicalPersonBindings": [],
    }
    for obj in objects:
        if obj.type.name != "MonoBehaviour" or reader.script_class(obj) != PERSON:
            continue
        coverage["personDefinitions"] += 1
        record = reader.mono(obj, PERSON)
        technical_name = record["articyTechnicalName"]
        if not technical_name:
            continue
        coverage["namedPersonBindings"] += 1
        binding = {
            "source": source(obj), "name": record["m_Name"], "guid": record["guid"]["guid"],
            "articyTechnicalName": technical_name, "articyVariableName": record["articyVariableName"],
            "variableBinding": binding_step(obj, "articyVariableName"),
            "hasStory": bool(record["hasStory"]),
        }
        if technical_name not in characters:
            binding["diagnostic"] = "not-a-canonical-articy-character"
            coverage["noncanonicalPersonBindings"].append(binding)
            continue
        portrait, via, diagnostic = person_portrait(reader, obj, record)
        binding["via"] = via
        characters[technical_name]["bindings"].append(binding)
        if portrait is None:
            binding["diagnostic"] = diagnostic
            continue
        descriptor = sprites.register(portrait)
        binding["portraitSource"] = descriptor["source"]
        key = (descriptor["source"]["file"], descriptor["source"]["pathId"])
        if key not in candidates[technical_name]:
            candidates[technical_name][key] = {**descriptor, "bindings": []}
        candidates[technical_name][key]["bindings"].append(binding)
        coverage["portraitBindings"] += 1

    for technical_name, character in sorted(characters.items()):
        variants = list(candidates[technical_name].values())
        if character["bindings"]:
            coverage["charactersWithBindings"] += 1
        if len(variants) == 1:
            character["portrait"] = variants[0]
            character["status"] = "bound"
            coverage["charactersWithPortrait"] += 1
        elif variants:
            character["variants"] = variants
            character["status"] = "conflict"
            character["diagnostics"] = ["multiple-authored-portraits"]
            coverage["conflicts"].append({
                "id": technical_name, "name": character["name"],
                "sources": [variant["source"] for variant in variants],
            })
        else:
            diagnostic = "missing-portrait" if character["bindings"] else "no-person-binding"
            character["status"] = "unresolved"
            character["diagnostics"] = [diagnostic]
            coverage["unresolvedCharacters"].append({
                "id": technical_name, "name": character["name"], "reason": diagnostic,
                "bindings": character["bindings"],
            })
        variables = sorted({
            binding["articyVariableName"] for binding in character["bindings"]
            if binding["articyVariableName"]
        })
        if len(variables) == 1:
            character["variableName"] = variables[0]
            coverage["charactersWithVariableName"] += 1
        elif len(variables) > 1:
            character["variableNames"] = variables
            character.setdefault("diagnostics", []).append("multiple-authored-variable-names")
            coverage["variableConflicts"].append({
                "id": technical_name, "name": character["name"], "variables": variables,
                "bindings": [
                    {**binding["variableBinding"], "value": binding["articyVariableName"]}
                    for binding in character["bindings"] if binding["articyVariableName"]
                ],
            })
    coverage["unresolvedCount"] = len(coverage["unresolvedCharacters"])
    coverage["conflictCount"] = len(coverage["conflicts"])
    coverage["variableConflictCount"] = len(coverage["variableConflicts"])
    return dict(sorted(characters.items())), coverage


class UnsupportedSpriteTrim(ValueError):
    pass


def restore_sprite_canvas(image, rect, offset):
    width, height = round(rect.width), round(rect.height)
    left, bottom = round(offset.x), round(offset.y)
    top = height - bottom - image.height
    if (
        width <= 0 or height <= 0 or left < 0 or top < 0
        or left + image.width > width or top + image.height > height
    ):
        raise UnsupportedSpriteTrim(
            f"Decoded {image.size} at ({left}, {top}) does not fit {width}x{height}"
        )
    canvas = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    canvas.paste(image.convert("RGBA"), (left, top))
    return canvas, {
        "left": left, "top": top, "decodedWidth": image.width, "decodedHeight": image.height,
    }


def vector(value, fields):
    return {field: getattr(value, field) for field in fields} if value is not None else None


def sprite_render_data(sprite, atlases):
    """Match UnityPy 1.25's atlas selection so trim offsets describe the decoded crop."""
    atlas_obj = None
    if sprite.m_SpriteAtlas:
        atlas_obj = sprite.m_SpriteAtlas.deref()
    elif sprite.m_AtlasTags:
        atlas_obj = next((
            obj for obj in sprite.assets_file.objects.values()
            if obj.type.name == "SpriteAtlas" and obj.peek_name() == sprite.m_AtlasTags[0]
        ), None)
    if atlas_obj is None:
        return sprite.m_RD, None
    key = object_key(atlas_obj)
    if key not in atlases:
        atlases[key] = atlas_obj.read()
    atlas = atlases[key]
    data = next(value for key, value in atlas.m_RenderDataMap if key == sprite.m_RenderDataKey)
    return data, source(atlas_obj)


class SpriteExporter:
    def __init__(self):
        self.sprites = {}
        self.pending = {}
        self.atlases = {}
        self.diagnostics = []

    def register(self, obj, *, required=True, inventory_reason=None):
        if obj.type.name != "Sprite":
            raise ValueError(f"Portrait/UI pointer is not a Sprite: {source(obj)}")
        src = source(obj)
        key = f"{src['file']}:{src['pathId']}"
        if key in self.sprites:
            pending = self.pending[key]
            pending["required"] |= required
            if inventory_reason:
                self.sprites[key]["inventoryReason"] = inventory_reason
            return self.sprites[key]
        sprite = obj.read()
        data, atlas_source = sprite_render_data(sprite, self.atlases)
        texture = data.texture.deref()
        alpha = data.alphaTexture.deref() if data.alphaTexture else None
        stem = "".join(c if c.isalnum() or c in "-_." else "_" for c in src["file"])
        descriptor = {
            "file": f"sprites/{stem}-{src['pathId']}.png", "name": sprite.m_Name,
            "width": round(sprite.m_Rect.width), "height": round(sprite.m_Rect.height),
            "source": src, "textureSource": source(texture),
            "rect": vector(sprite.m_Rect, ("x", "y", "width", "height")),
            "pivot": vector(sprite.m_Pivot, ("x", "y")),
            "trimRect": vector(data.textureRect, ("x", "y", "width", "height")),
            "trimOffset": vector(data.textureRectOffset, ("x", "y")),
            "border": (
                dict(zip(("left", "bottom", "right", "top"),
                         (sprite.m_Border.x, sprite.m_Border.y, sprite.m_Border.z, sprite.m_Border.w)))
                if sprite.m_Border is not None else None
            ),
            "pixelsPerUnit": sprite.m_PixelsToUnits,
            "settingsRaw": data.settingsRaw,
            "renderDataSource": atlas_source or src,
        }
        if inventory_reason:
            descriptor["inventoryReason"] = inventory_reason
        self.sprites[key] = descriptor
        self.pending[key] = {
            "sprite": sprite, "data": data, "required": required,
            "textureKey": (object_key(texture), object_key(alpha) if alpha else None),
        }
        return descriptor

    def export(self, directory):
        groups = defaultdict(list)
        for key, pending in self.pending.items():
            groups[pending["textureKey"]].append(key)
        for keys in groups.values():
            owners = {id(self.pending[key]["sprite"].assets_file):
                      self.pending[key]["sprite"].assets_file for key in keys}
            # UnityPy caches decoded textures on the sprite owner. Bound memory to one atlas.
            for owner in owners.values():
                owner._cache.clear()
            try:
                for key in keys:
                    pending = self.pending[key]
                    sprite = pending["sprite"]
                    try:
                        decoded = sprite.image
                        image, restoration = restore_sprite_canvas(
                            decoded, sprite.m_Rect, pending["data"].textureRectOffset,
                        )
                    except UnsupportedSpriteTrim as error:
                        if pending["required"]:
                            raise UnsupportedSpriteTrim(f"{self.sprites[key]['source']}: {error}") from error
                        self.diagnostics.append({
                            "source": self.sprites[key]["source"], "name": sprite.m_Name,
                            "reason": "unsupported-trim", "detail": str(error),
                        })
                        del self.sprites[key]
                        continue
                    descriptor = self.sprites[key]
                    descriptor["restoration"] = restoration
                    path = directory / descriptor["file"]
                    path.parent.mkdir(parents=True, exist_ok=True)
                    image.save(path, format="PNG")
                    image.close()
                    decoded.close()
            finally:
                for owner in owners.values():
                    owner._cache.clear()


def serialized_color(color):
    rgba = [color[channel] for channel in ("r", "g", "b", "a")]
    if not all(0 <= value <= 1 for value in rgba):
        raise ValueError(f"Unsupported serialized UI color: {rgba}")
    return "#" + "".join(f"{round(value * 255):02X}" for value in rgba[:3]), rgba


def image_descriptor(reader, sprites, image_obj, via):
    record = reader.mono(image_obj, UI_IMAGE)
    sprite_obj = resolve_pointer(image_obj, record["m_Sprite"])
    if sprite_obj is None:
        raise ValueError(f"Expected bound UI sprite at {source(image_obj)}")
    descriptor = sprites.register(sprite_obj)
    tint, rgba = serialized_color(record["m_Color"])
    return {
        **descriptor, "tint": tint, "rgba": rgba,
        "tintBinding": binding_step(image_obj, "m_Color"),
        "binding": [*via, binding_step(image_obj, "m_Sprite")],
        "imageType": record["m_Type"],
        "pixelsPerUnitMultiplier": record["m_PixelsPerUnitMultiplier"],
    }


def portrait_children(reader, group):
    """CanvasGroup is native; its GameObject owns the child RectTransforms."""
    group_record = reader.native(group, "CanvasGroup")
    game_object = resolve_pointer(group, group_record["m_GameObject"])
    game_record = reader.native(game_object, "GameObject")
    transforms = []
    for component in game_record["m_Component"]:
        obj = resolve_pointer(game_object, component["component"])
        if obj.type.name == "RectTransform":
            transforms.append(obj)
    if len(transforms) != 1:
        raise ValueError("Dialogue portrait parent does not have exactly one RectTransform")
    root = transforms[0]
    queue = [(root, [binding_step(group, "m_GameObject"),
                     binding_step(game_object, "m_Component")])]
    visited = set()
    while queue:
        transform, via = queue.pop(0)
        key = object_key(transform)
        if key in visited:
            raise ValueError("Cycle in portrait RectTransform hierarchy")
        visited.add(key)
        record = reader.native(transform, "RectTransform")
        for child_pointer in record["m_Children"]:
            child = resolve_pointer(transform, child_pointer)
            child_record = reader.native(child, "RectTransform")
            go = resolve_pointer(child, child_record["m_GameObject"])
            go_record = reader.native(go, "GameObject")
            child_via = [*via, binding_step(transform, "m_Children"),
                         binding_step(child, "m_GameObject")]
            yield go, go_record, child_via
            queue.append((child, child_via))


def collect_ui(objects, reader, sprites):
    by_class = defaultdict(list)
    for obj in objects:
        if obj.type.name == "MonoBehaviour":
            class_name = reader.script_class(obj)
            if class_name in {DIALOGUE_UI, JOURNAL_ENTRY, JOURNAL_LIST, CHARACTER_WIDGET}:
                by_class[class_name].append(obj)
    if len(by_class[DIALOGUE_UI]) != 1:
        raise ValueError("Expected exactly one authored DialogueUI in sharedassets0.assets")
    dialogue = by_class[DIALOGUE_UI][0]
    record = reader.mono(dialogue, DIALOGUE_UI)
    matrix = resolve_pointer(dialogue, record["miniEmotionMatrix"])
    matrix_record = reader.mono(matrix, MATRIX)
    relationships = {}
    for key, (meter, field) in METERS.items():
        widget = resolve_pointer(matrix, matrix_record[field])
        widget_record = reader.mono(widget, WIDGET)
        image = resolve_pointer(widget, widget_record["icon"])
        relationships[key] = {
            **image_descriptor(reader, sprites, image, [
                binding_step(dialogue, "miniEmotionMatrix"), binding_step(matrix, field),
                binding_step(widget, "icon"),
            ]),
            "meter": meter, "tintContext": "DialogueUI.MiniRelationMatrix",
            "tintKind": "serialized-prefab-color; runtime animations may override",
        }
    for widget in by_class[CHARACTER_WIDGET]:
        widget_record = reader.mono(widget, CHARACTER_WIDGET)
        for key in METERS:
            field = key + "Image"
            sprite = resolve_pointer(widget, widget_record[field])
            tint, rgba = serialized_color(widget_record[field + "Color"])
            relationships[key].setdefault("alternateBindings", []).append({
                "context": "CharacterWindowWidgetCharacter",
                "source": source(widget), "field": field, "colorField": field + "Color",
                "spriteSource": source(sprite), "tint": tint, "rgba": rgba,
            })
    bubble = resolve_pointer(dialogue, record["bubble"])
    ui = {"dialogueBubble": image_descriptor(
        reader, sprites, bubble, [binding_step(dialogue, "bubble")],
    )}
    parent = resolve_pointer(dialogue, record["portraitParent"])
    roles = {"PortraitBorder": "portraitBorder", "Portrait_Mask (1)": "portraitMask"}
    for go, go_record, via in portrait_children(reader, parent):
        role = roles.get(go_record["m_Name"])
        if not role:
            continue
        images = []
        for component in go_record["m_Component"]:
            obj = resolve_pointer(go, component["component"])
            if obj.type.name == "MonoBehaviour" and reader.script_class(obj) == UI_IMAGE:
                images.append(obj)
        if len(images) != 1 or role in ui:
            raise ValueError(f"Ambiguous dialogue portrait role: {role}")
        ui[role] = image_descriptor(
            reader, sprites, images[0],
            [binding_step(dialogue, "portraitParent"), *via, binding_step(go, "m_Component")],
        )
    if not {"portraitBorder", "portraitMask"} <= ui.keys():
        raise ValueError("Missing verified dialogue portrait border/mask bindings")
    # The two entry prefabs differ; record all bindings instead of choosing a sample.
    for entry in by_class[JOURNAL_ENTRY]:
        entry_record = reader.mono(entry, JOURNAL_ENTRY)
        for role, field in (("questBadge", "numberBgImage"), ("questCheckMark", "checkMarkImage")):
            image = resolve_pointer(entry, entry_record[field])
            if image is None:
                continue
            descriptor = image_descriptor(reader, sprites, image, [binding_step(entry, field)])
            descriptor["questColors"] = {
                name: dict(zip(("tint", "rgba"), serialized_color(entry_record[name])))
                for name in ("regularQuestColor", "pinnedQuestColor")
            }
            add_ui_variant(ui, role, descriptor)
    for item in by_class[JOURNAL_LIST]:
        item_record = reader.mono(item, JOURNAL_LIST)
        for field in ("questIconNumbered", "questIconUnnumbered"):
            sprite = resolve_pointer(item, item_record[field])
            descriptor = {**sprites.register(sprite), "binding": [binding_step(item, field)]}
            add_ui_variant(ui, field, descriptor)
    portrait_image = resolve_pointer(dialogue, record["portraitImage"])
    portrait_record = reader.mono(portrait_image, UI_IMAGE)
    dynamic_sprite = resolve_pointer(portrait_image, portrait_record["m_Sprite"])
    provenance = {
        "dialogueUI": source(dialogue),
        "portraitImage": {
            "source": source(portrait_image),
            "sprite": source(dynamic_sprite) if dynamic_sprite is not None else None,
            "mode": "dynamic-from-person",
            "binding": [binding_step(dialogue, "portraitImage")],
        },
    }
    return relationships, ui, provenance


def add_ui_variant(ui, role, descriptor):
    if role not in ui:
        ui[role] = descriptor
    elif ui[role]["source"] == descriptor["source"]:
        ui[role].setdefault("additionalBindings", []).append(descriptor["binding"])
    else:
        # The semantic role is shared, but the authored UI prefabs use different art.
        ui[role].setdefault("variants", []).append(descriptor)


def validate_output_directory(game_dir, output_dir):
    requested_output = Path(output_dir).absolute()
    if any(path.is_symlink() or path.is_junction() for path in (requested_output, *requested_output.parents)):
        raise ValueError("Media output must not traverse links or junctions")
    output_dir = requested_output.resolve()
    game_dir = Path(game_dir).resolve()
    if output_dir == game_dir or game_dir in output_dir.parents or output_dir in game_dir.parents:
        raise ValueError("Media output must not overlap the game/source directory")
    root = OUTPUT_ROOT.resolve()
    if output_dir == root or root not in output_dir.parents:
        raise ValueError("Media output must be a subdirectory of the Git-ignored project output directory")
    if output_dir.exists() and not output_dir.is_dir():
        raise ValueError("Media output is not a directory")
    manifest_path = output_dir / "manifest.json"
    owned = set()
    if output_dir.exists() and any(output_dir.iterdir()):
        if manifest_path.is_symlink() or not manifest_path.is_file():
            raise ValueError("Refusing to overwrite a nonempty, unowned media output directory")
        previous = json.loads(manifest_path.read_text(encoding="utf-8"))
        if previous.get("generator") != "extract_media.py" or previous.get("schemaVersion") != 2:
            raise ValueError("Refusing to overwrite an unowned media manifest")
        for descriptor in previous["sprites"].values():
            relative = descriptor["file"]
            if not isinstance(relative, str):
                raise ValueError("Previous media manifest contains an unsafe sprite path")
            path = output_dir / relative
            if (
                "\\" in relative
                or not relative.startswith("sprites/") or not relative.endswith(".png")
                or len(Path(relative).parts) != 2
                or any(parent.is_symlink() or parent.is_junction()
                       for parent in (path, *path.parents) if parent != output_dir)
                or output_dir not in path.resolve().parents
            ):
                raise ValueError("Previous media manifest contains an unsafe sprite path")
            owned.add(path.resolve())
    return output_dir, owned


def export_media(assets: ArticyAssets, output_dir: Path) -> dict:
    """Export only authored media into ignored output; never mutate the source install."""
    output_dir, owned = validate_output_directory(assets.game_dir, output_dir)
    OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
    staging = OUTPUT_ROOT / f".media-{uuid.uuid4().hex}"
    staging.mkdir()
    try:
        # A path load can materialize the huge .assets in RAM; keep this stream live instead.
        with (assets.data_dir / "sharedassets0.assets").open("rb") as stream:
            env = UnityPy.load(stream)
            env.path = str(assets.data_dir)
            generator = assets.generator
            if not isinstance(generator, LocalTypeTreeGenerator):
                raise TypeError("Media extraction requires the shared LocalTypeTreeGenerator")
            env.typetree_generator = generator
            objects = list(env.objects)
            reader, sprites = MediaReader(), SpriteExporter()
            characters, coverage = collect_characters(assets, objects, reader, sprites)
            relationships, ui, provenance = collect_ui(objects, reader, sprites)
            for obj in objects:
                if obj.type.name != "Sprite":
                    continue
                name = obj.peek_name()
                if name.startswith(("9S_", "9s_", "T_Icons_")):
                    sprites.register(obj, required=False, inventory_reason="ui-name-prefix")
                elif name in {"T_QuestTrackingOutline", "T_QuestTrackingFilling"}:
                    sprites.register(obj, required=False, inventory_reason="frame-inventory-only")
            sprites.export(staging)
            # Role descriptors are copies; propagate restoration metadata after rendering.
            def hydrate(value):
                if isinstance(value, dict):
                    if "file" in value and "width" in value and "source" in value:
                        key = f"{value['source']['file']}:{value['source']['pathId']}"
                        value["restoration"] = sprites.sprites[key]["restoration"]
                    for child in value.values():
                        hydrate(child)
                elif isinstance(value, list):
                    for child in value:
                        hydrate(child)
            hydrate([characters, relationships, ui])
            manifest = {
                "schemaVersion": 2, "generator": "extract_media.py",
                "source": {"file": "sharedassets0.assets"},
                "characters": characters, "relationships": relationships, "ui": ui,
                "sprites": dict(sorted(sprites.sprites.items())),
                "coverage": coverage, "provenance": provenance,
                "diagnostics": sprites.diagnostics,
            }
            (staging / "manifest.json").write_text(
                json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
            )
        _, current_owned = validate_output_directory(assets.game_dir, output_dir)
        if current_owned != owned:
            raise ValueError("Media output changed during extraction; refusing to replace files")
        output_dir.mkdir(parents=True, exist_ok=True)
        files = [p for p in staging.rglob("*") if p.is_file()]
        files.sort(key=lambda p: p.name == "manifest.json")
        # Check every destination before replacing any file; never remove unrelated output.
        for path in files:
            target = output_dir / path.relative_to(staging)
            if (
                target.is_symlink() or target.is_junction()
                or target.parent.is_symlink() or target.parent.is_junction()
                or output_dir not in target.resolve().parents
            ):
                raise ValueError("Unsafe media destination")
            if target.exists() and target.name != "manifest.json" and target.resolve() not in owned:
                raise ValueError(f"Refusing to overwrite an unowned sprite: {target.name}")
        for path in files:
            target = output_dir / path.relative_to(staging)
            target.parent.mkdir(parents=True, exist_ok=True)
            path.replace(target)
        return manifest
    finally:
        shutil.rmtree(staging)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--game-dir", type=Path, default=DEFAULT_GAME)
    parser.add_argument(
        "--output-dir", type=Path, default=OUTPUT_ROOT / "media",
        help="Directory beneath this project's Git-ignored output directory (default: output\\media)",
    )
    args = parser.parse_args()
    validate_output_directory(args.game_dir, args.output_dir)
    manifest = export_media(ArticyAssets(args.game_dir), args.output_dir)
    coverage = manifest["coverage"]
    print(
        f"Exported {len(manifest['sprites'])} sprites; "
        f"{coverage['charactersWithPortrait']}/{coverage['canonicalCharacters']} character portraits, "
        f"{coverage['unresolvedCount']} unresolved, {coverage['conflictCount']} conflicts."
    )


if __name__ == "__main__":
    main()
