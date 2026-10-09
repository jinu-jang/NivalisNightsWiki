import json
from pathlib import Path
import shutil
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock, patch
import uuid

from PIL import Image

from extract_media import (
    AGENT, CHARACTER, LocalTypeTreeGenerator, MediaReader, OUTPUT_ROOT, PERSON, SpriteExporter,
    UnsupportedSpriteTrim, add_ui_variant, collect_characters, export_media, person_portrait, portrait_children,
    resolve_pointer, serialized_color, source, sprite_render_data, restore_sprite_canvas,
    validate_output_directory,
)


def pointer(path_id=0, file_id=0):
    return {"m_FileID": file_id, "m_PathID": path_id}


def file_owner(name="sharedassets0.assets"):
    return NS(name=name, objects={}, externals=[], parent=NS(files={}), _cache={})


class FakeObject:
    def __init__(self, owner, path_id, record=None, class_name=None, native="MonoBehaviour", script_id=1):
        self.assets_file = owner
        self.path_id = path_id
        self.type = NS(name=native)
        self.record = record or {}
        self.class_name = class_name
        self.script_id = script_id
        self.script_reads = 0
        self.tree_reads = 0
        owner.objects[path_id] = self

    def read_typetree(self):
        self.tree_reads += 1
        return self.record

    def parse_monobehaviour_head(self):
        def read_script():
            self.script_reads += 1
            return NS(m_Namespace=self.class_name[0], m_ClassName=self.class_name[1])
        return NS(m_Script=NS(
            m_FileID=1, m_PathID=self.script_id, deref_parse_as_object=read_script,
        ))


class FakeSprites:
    def register(self, obj):
        return {
            "file": f"sprites/{obj.path_id}.png", "width": 128, "height": 128,
            "source": source(obj),
        }


def person_record(technical_name="nola", prefab=0, override=0, variable_name="NolaBerg"):
    return {
        "m_Name": "Person", "guid": {"guid": "authored-guid"},
        "articyTechnicalName": technical_name, "articyVariableName": variable_name,
        "hasStory": 1, "CharacterPrefab": pointer(prefab), "overridePortrait": pointer(override),
    }


def character_assets():
    records = {
        2**54: {"technicalName": "nola", "mDisplayName": "Nola Berg"},
        2**54 + 1: {"technicalName": "ava", "mDisplayName": "Ava"},
    }
    return NS(
        classes={identifier: "DefaultSupportingCharacterTemplate" for identifier in records},
        read=records.__getitem__,
        source=lambda identifier: {"file": "resources.assets", "pathId": str(identifier)},
    )


class SpriteRestorationTests(unittest.TestCase):
    def test_trimmed_ava_uses_rounded_left_offset_and_preserves_alpha(self):
        crop = Image.new("RGBA", (244, 256), (30, 40, 50, 127))
        restored, metadata = restore_sprite_canvas(
            crop, NS(width=256, height=256), NS(x=12.1888, y=0),
        )
        self.assertEqual(restored.size, (256, 256))
        self.assertEqual(restored.getpixel((11, 0)), (0, 0, 0, 0))
        self.assertEqual(restored.getpixel((12, 0)), (30, 40, 50, 127))
        self.assertEqual(restored.getpixel((255, 255)), (30, 40, 50, 127))
        self.assertEqual(metadata, {
            "left": 12, "top": 0, "decodedWidth": 244, "decodedHeight": 256,
        })

    def test_nola_restores_missing_top_row_without_stretching(self):
        restored, _ = restore_sprite_canvas(
            Image.new("RGBA", (128, 127), (1, 2, 3, 255)),
            NS(width=128, height=128), NS(x=0, y=0),
        )
        self.assertEqual(restored.size, (128, 128))
        self.assertEqual(restored.getpixel((0, 0))[3], 0)
        self.assertEqual(restored.getpixel((0, 1)), (1, 2, 3, 255))

    def test_friend_offsets_convert_unity_bottom_to_image_top(self):
        restored, metadata = restore_sprite_canvas(
            Image.new("RGBA", (244, 148), (1, 2, 3, 255)),
            NS(width=256, height=256), NS(x=6.0761, y=54.0762),
        )
        self.assertEqual(metadata["left"], 6)
        self.assertEqual(metadata["top"], 54)
        self.assertEqual(restored.getbbox(), (6, 54, 250, 202))

    def test_non_symmetric_bottom_offset_does_not_get_used_as_top(self):
        restored, metadata = restore_sprite_canvas(
            Image.new("RGBA", (2, 3), (1, 2, 3, 255)),
            NS(width=8, height=10), NS(x=1, y=2),
        )
        self.assertEqual(metadata["top"], 5)
        self.assertEqual(restored.getbbox(), (1, 5, 3, 8))

    def test_out_of_canvas_crops_are_rejected_not_silently_clipped(self):
        for offset in (NS(x=-1, y=0), NS(x=1, y=0), NS(x=0, y=1), NS(x=0, y=-1)):
            with self.subTest(offset=offset), self.assertRaises(UnsupportedSpriteTrim):
                restore_sprite_canvas(Image.new("RGBA", (8, 8)), NS(width=8, height=8), offset)

    def test_atlas_render_data_takes_precedence_over_sprite_render_data(self):
        owner = file_owner()
        atlas_obj = FakeObject(owner, 7, native="SpriteAtlas")
        atlas_obj.read = lambda: NS(m_RenderDataMap=[("sprite-key", NS(textureRectOffset=NS(x=4, y=8)))])
        sprite = NS(
            m_SpriteAtlas=NS(deref=lambda: atlas_obj),
            m_RenderDataKey="sprite-key", m_RD=NS(textureRectOffset=NS(x=0, y=0)),
        )
        render_data, origin = sprite_render_data(sprite, {})
        self.assertEqual(render_data.textureRectOffset.x, 4)
        self.assertEqual(origin, {"file": "sharedassets0.assets", "pathId": "7"})

    def test_atlas_tag_fallback_matches_unitypy(self):
        owner = file_owner()
        atlas_obj = FakeObject(owner, 8, native="SpriteAtlas")
        atlas_obj.peek_name = lambda: "portrait-atlas"
        actual = NS(textureRectOffset=NS(x=7, y=9))
        atlas_obj.read = lambda: NS(m_RenderDataMap=[("key", actual)])
        sprite = NS(
            m_SpriteAtlas=None, m_AtlasTags=["portrait-atlas"], assets_file=owner,
            m_RenderDataKey="key", m_RD=NS(),
        )
        data, origin = sprite_render_data(sprite, {})
        self.assertIs(data, actual)
        self.assertEqual(origin["pathId"], "8")


class MediaBindingTests(unittest.TestCase):
    def test_override_wins_without_decoding_prefab(self):
        owner = file_owner()
        sprite = FakeObject(owner, 5, native="Sprite")
        person = FakeObject(owner, 1, person_record(prefab=999, override=5), PERSON)
        portrait, via, diagnostic = person_portrait(MediaReader(), person, person.record)
        self.assertIs(portrait, sprite)
        self.assertIsNone(diagnostic)
        self.assertEqual([step["field"] for step in via], ["overridePortrait"])

    def test_prefab_agent_character_sprite_chain_is_authored(self):
        owner = file_owner()
        sprite = FakeObject(owner, 5, native="Sprite")
        FakeObject(owner, 4, {"portrait": pointer(5)}, CHARACTER, script_id=3)
        FakeObject(owner, 3, {"character": pointer(4)}, AGENT, script_id=2)
        person = FakeObject(owner, 1, person_record(prefab=3), PERSON)
        portrait, via, diagnostic = person_portrait(MediaReader(), person, person.record)
        self.assertIs(portrait, sprite)
        self.assertIsNone(diagnostic)
        self.assertEqual([step["field"] for step in via], ["CharacterPrefab", "character", "portrait"])
        self.assertEqual([step["source"]["pathId"] for step in via], ["1", "3", "4"])

    def test_cross_file_pointer_uses_owning_assets_file(self):
        first, second = file_owner("first.assets"), file_owner("second.assets")
        first.externals = [NS(path="archive:/assets/second.assets")]
        first.parent.files = {"second.assets": second}
        wrong = FakeObject(first, 5, native="Sprite")
        correct = FakeObject(second, 5, native="Sprite")
        obj = FakeObject(first, 1)
        resolved = resolve_pointer(obj, pointer(5, 1))
        self.assertIs(resolved, correct)
        self.assertIsNot(resolved, wrong)
        self.assertIsNone(resolve_pointer(obj, pointer()))

    def test_script_cache_does_not_cross_file_owner_boundaries(self):
        reader = MediaReader()
        first = FakeObject(file_owner(), 1, class_name=PERSON)
        second = FakeObject(file_owner(), 1, class_name=AGENT)
        self.assertEqual(reader.script_class(first), PERSON)
        self.assertEqual(reader.script_class(second), AGENT)
        self.assertEqual(reader.script_class(first), PERSON)
        self.assertEqual(first.script_reads, 1)
        self.assertEqual(second.script_reads, 1)

    def test_unverified_classes_and_unexpected_bindings_are_rejected(self):
        reader = MediaReader()
        obj = FakeObject(file_owner(), 1, class_name=("Other", "Something"))
        with self.assertRaisesRegex(ValueError, "Unverified"):
            reader.mono(obj, ("Other", "Something"))
        with self.assertRaisesRegex(ValueError, "Unexpected component"):
            reader.mono(obj, PERSON)
        self.assertEqual(obj.tree_reads, 0)

    def test_typetree_failures_are_not_swallowed(self):
        obj = FakeObject(file_owner(), 1, class_name=PERSON)
        obj.read_typetree = lambda: (_ for _ in ()).throw(ValueError("bad typetree"))
        with self.assertRaisesRegex(ValueError, "bad typetree"):
            MediaReader().mono(obj, PERSON)

    def test_differing_authored_portraits_preserve_variants_and_no_primary(self):
        owner = file_owner()
        FakeObject(owner, 5, native="Sprite")
        FakeObject(owner, 6, native="Sprite")
        one = FakeObject(owner, 1, person_record(override=5), PERSON)
        two = FakeObject(owner, 2, person_record(override=6), PERSON)
        characters, coverage = collect_characters(
            character_assets(), [one, two], MediaReader(), FakeSprites(),
        )
        self.assertIsNone(characters["nola"]["portrait"])
        self.assertEqual(characters["nola"]["status"], "conflict")
        self.assertEqual(len(characters["nola"]["variants"]), 2)
        self.assertEqual(coverage["conflictCount"], 1)
        self.assertEqual(coverage["portraitBindings"], 2)
        self.assertEqual(coverage["unresolvedCharacters"][0]["id"], "ava")
        self.assertEqual(characters["nola"]["articyId"], str(2**54))

    def test_duplicate_same_sprite_bindings_deduplicate_without_conflict(self):
        owner = file_owner()
        FakeObject(owner, 5, native="Sprite")
        people = [FakeObject(owner, n, person_record(override=5), PERSON) for n in (1, 2)]
        characters, coverage = collect_characters(
            character_assets(), people, MediaReader(), FakeSprites(),
        )
        self.assertEqual(len(characters["nola"]["portrait"]["bindings"]), 2)
        self.assertEqual(coverage["conflictCount"], 0)
        self.assertEqual(coverage["charactersWithPortrait"], 1)
        self.assertEqual(characters["nola"]["variableName"], "NolaBerg")
        self.assertEqual(coverage["charactersWithVariableName"], 1)
        self.assertEqual(coverage["variableConflictCount"], 0)

    def test_variable_mapping_is_authored_even_when_portrait_is_missing(self):
        owner = file_owner()
        person = FakeObject(owner, 1, person_record(variable_name="Serialized_Nola"), PERSON)
        characters, coverage = collect_characters(
            character_assets(), [person], MediaReader(), FakeSprites(),
        )
        self.assertEqual(characters["nola"]["variableName"], "Serialized_Nola")
        self.assertIsNone(characters["nola"]["portrait"])
        self.assertIsNone(characters["ava"]["variableName"])
        self.assertEqual(characters["nola"]["bindings"][0]["variableBinding"], {
            "source": {"file": "sharedassets0.assets", "pathId": "1"},
            "field": "articyVariableName",
        })
        self.assertEqual(coverage["charactersWithVariableName"], 1)

    def test_conflicting_authored_variables_are_null_and_preserved_not_last_wins(self):
        owner = file_owner()
        FakeObject(owner, 5, native="Sprite")
        people = [
            FakeObject(owner, 1, person_record(override=5, variable_name="NolaAlternate"), PERSON),
            FakeObject(owner, 2, person_record(override=5), PERSON),
        ]
        characters, coverage = collect_characters(
            character_assets(), people, MediaReader(), FakeSprites(),
        )
        character = characters["nola"]
        self.assertIsNone(character["variableName"])
        self.assertEqual(character["variableNames"], ["NolaAlternate", "NolaBerg"])
        self.assertEqual(character["status"], "bound")
        self.assertEqual(character["diagnostics"], ["multiple-authored-variable-names"])
        self.assertEqual(coverage["charactersWithVariableName"], 0)
        self.assertEqual(coverage["variableConflictCount"], 1)
        self.assertEqual([binding["value"] for binding in coverage["variableConflicts"][0]["bindings"]],
                         ["NolaAlternate", "NolaBerg"])
        self.assertEqual(coverage["variableConflicts"][0]["bindings"][1]["source"]["pathId"], "2")

    def test_empty_variables_do_not_get_guessed_from_display_name(self):
        owner = file_owner()
        person = FakeObject(owner, 1, person_record(variable_name=""), PERSON)
        characters, coverage = collect_characters(
            character_assets(), [person], MediaReader(), FakeSprites(),
        )
        self.assertIsNone(characters["nola"]["variableName"])
        self.assertEqual(coverage["charactersWithVariableName"], 0)
        self.assertEqual(coverage["variableConflictCount"], 0)

    def test_empty_binding_does_not_discard_unique_nonempty_authored_variable(self):
        owner = file_owner()
        people = [
            FakeObject(owner, 1, person_record(variable_name=""), PERSON),
            FakeObject(owner, 2, person_record(), PERSON),
        ]
        characters, coverage = collect_characters(
            character_assets(), people, MediaReader(), FakeSprites(),
        )
        self.assertEqual(characters["nola"]["variableName"], "NolaBerg")
        self.assertEqual(coverage["variableConflictCount"], 0)

    def test_missing_and_noncanonical_bindings_stay_explicit_not_fuzzy_matched(self):
        owner = file_owner()
        people = [
            FakeObject(owner, 1, person_record(), PERSON),
            FakeObject(owner, 2, person_record("Nola Berg"), PERSON),
            FakeObject(owner, 3, person_record(""), PERSON),
        ]
        characters, coverage = collect_characters(
            character_assets(), people, MediaReader(), FakeSprites(),
        )
        self.assertEqual(coverage["personDefinitions"], 3)
        self.assertEqual(coverage["namedPersonBindings"], 2)
        self.assertEqual(coverage["charactersWithBindings"], 1)
        self.assertEqual(coverage["unresolvedCount"], 2)
        self.assertIsNone(characters["nola"]["portrait"])
        self.assertEqual(characters["nola"]["bindings"][0]["diagnostic"], "missing-character-prefab")
        self.assertEqual(coverage["noncanonicalPersonBindings"][0]["articyTechnicalName"], "Nola Berg")
        self.assertIsNone(characters["ava"]["variableName"])

    def test_native_canvas_group_traversal_does_not_decode_as_monobehaviour(self):
        owner = file_owner()
        group = FakeObject(owner, 1, {"m_GameObject": pointer(2)}, native="CanvasGroup")
        FakeObject(owner, 2, {"m_Component": [{"component": pointer(3)}]}, native="GameObject")
        FakeObject(owner, 3, {"m_Children": [pointer(4)]}, native="RectTransform")
        FakeObject(owner, 4, {"m_GameObject": pointer(5), "m_Children": []}, native="RectTransform")
        FakeObject(owner, 5, {"m_Name": "PortraitBorder"}, native="GameObject")
        children = list(portrait_children(MediaReader(), group))
        self.assertEqual(children[0][1]["m_Name"], "PortraitBorder")
        self.assertEqual(group.script_reads, 0)

    def test_source_ids_are_strings_and_paths_are_filenames_only(self):
        obj = FakeObject(file_owner(r"C:\Users\private\game\sharedassets0.assets"), 2**60)
        self.assertEqual(source(obj), {"file": "sharedassets0.assets", "pathId": str(2**60)})

    def test_serialized_tints_are_not_assumed_runtime_colors(self):
        tint, rgba = serialized_color({
            "r": 0.227450981736, "g": 0.458823531866, "b": 0.250980406999, "a": 1,
        })
        self.assertEqual(tint, "#3A7540")
        self.assertEqual(len(rgba), 4)
        with self.assertRaises(ValueError):
            serialized_color({"r": 2, "g": 0, "b": 0, "a": 1})

    def test_distinct_ui_role_artwork_keeps_variant_provenance(self):
        first = {"source": {"file": "a.assets", "pathId": "1"}, "binding": [{"field": "one"}]}
        second = {"source": {"file": "a.assets", "pathId": "2"}, "binding": [{"field": "two"}]}
        ui = {}
        add_ui_variant(ui, "badge", first)
        add_ui_variant(ui, "badge", second)
        self.assertEqual(ui["badge"]["variants"][0]["source"]["pathId"], "2")
        self.assertEqual(ui["badge"]["variants"][0]["binding"][0]["field"], "two")


class SpriteExportTests(unittest.TestCase):
    def setUp(self):
        self.root = OUTPUT_ROOT / f"media-test-{uuid.uuid4().hex}"
        self.root.mkdir(parents=True)
        self.owner = file_owner()
        self.texture = FakeObject(self.owner, 10, native="Texture2D")

    def tearDown(self):
        shutil.rmtree(self.root)

    def sprite(self, path_id=20, *, offset_x=1, texture=None):
        obj = FakeObject(self.owner, path_id, native="Sprite")
        texture = texture or self.texture
        image = Image.new("RGBA", (2, 3), (10, 20, 30, 127))
        data = NS(
            texture=NS(deref=lambda: texture), alphaTexture=None,
            textureRect=NS(x=0, y=0, width=2, height=3),
            textureRectOffset=NS(x=offset_x, y=2), settingsRaw=64,
        )
        sprite = NS(
            assets_file=self.owner, image=image,
            m_Name="synthetic", m_Rect=NS(x=0, y=0, width=8, height=10),
            m_Pivot=NS(x=0.5, y=0.5), m_RD=data, m_SpriteAtlas=None, m_AtlasTags=[],
            m_Border=NS(x=1, y=2, z=3, w=4), m_PixelsToUnits=100,
        )
        obj.read = lambda: sprite
        return obj

    def test_png_restoration_and_nine_slice_metadata_survive_export(self):
        exporter = SpriteExporter()
        descriptor = exporter.register(self.sprite())
        exporter.export(self.root)
        with Image.open(self.root / descriptor["file"]) as image:
            self.assertEqual(image.size, (8, 10))
            self.assertEqual(image.mode, "RGBA")
            self.assertEqual(image.getpixel((1, 5)), (10, 20, 30, 127))
        self.assertEqual(descriptor["border"], {"left": 1, "bottom": 2, "right": 3, "top": 4})
        self.assertEqual(descriptor["restoration"]["top"], 5)
        self.assertEqual(descriptor["textureSource"]["pathId"], "10")
        self.assertEqual(self.owner._cache, {})

    def test_sprite_registration_deduplicates_and_required_binding_wins(self):
        exporter = SpriteExporter()
        obj = self.sprite()
        optional = exporter.register(obj, required=False, inventory_reason="ui-name-prefix")
        required = exporter.register(obj, required=True)
        self.assertIs(optional, required)
        self.assertEqual(len(exporter.sprites), 1)
        self.assertTrue(next(iter(exporter.pending.values()))["required"])

    def test_unsupported_optional_inventory_is_explicitly_reported(self):
        exporter = SpriteExporter()
        descriptor = exporter.register(self.sprite(offset_x=9), required=False)
        exporter.export(self.root)
        self.assertEqual(exporter.sprites, {})
        self.assertEqual(exporter.diagnostics[0]["reason"], "unsupported-trim")
        self.assertFalse((self.root / descriptor["file"]).exists())

    def test_unsupported_required_portrait_raises_with_safe_source(self):
        exporter = SpriteExporter()
        exporter.register(self.sprite(offset_x=9))
        with self.assertRaisesRegex(UnsupportedSpriteTrim, "sharedassets0.assets"):
            exporter.export(self.root)
        self.assertEqual(self.owner._cache, {})


class OutputSafetyTests(unittest.TestCase):
    def setUp(self):
        self.root = OUTPUT_ROOT / f"media-test-{uuid.uuid4().hex}"
        self.root.mkdir(parents=True)
        self.game = Path(__file__).resolve().parents[1] / "game-test-not-created"

    def tearDown(self):
        shutil.rmtree(self.root)

    def test_game_source_and_project_source_are_rejected(self):
        for output in (self.game, self.game / "media", self.game.parent, OUTPUT_ROOT):
            with self.subTest(output=output), self.assertRaises(ValueError):
                validate_output_directory(self.game, output)

    def test_empty_ignored_output_is_allowed(self):
        output, owned = validate_output_directory(self.game, self.root)
        self.assertEqual(output, self.root.resolve())
        self.assertEqual(owned, set())

    def test_nonempty_arbitrary_output_is_never_erased(self):
        unrelated = self.root / "keep.txt"
        unrelated.write_text("keep me", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "unowned"):
            validate_output_directory(self.game, self.root)
        self.assertEqual(unrelated.read_text(encoding="utf-8"), "keep me")

    def test_foreign_manifest_is_rejected(self):
        (self.root / "manifest.json").write_text('{"schemaVersion": 2}', encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "unowned"):
            validate_output_directory(self.game, self.root)

    def test_owned_manifest_cannot_authorize_parent_path_overwrite(self):
        (self.root / "manifest.json").write_text(json.dumps({
            "schemaVersion": 2, "generator": "extract_media.py",
            "sprites": {"bad": {"file": "../keep.png"}},
        }), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "unsafe"):
            validate_output_directory(self.game, self.root)

    def test_owned_reexport_permits_only_previous_generated_paths(self):
        (self.root / "manifest.json").write_text(json.dumps({
            "schemaVersion": 2, "generator": "extract_media.py",
            "sprites": {"sprite": {"file": "sprites/5.png"}},
        }), encoding="utf-8")
        output, owned = validate_output_directory(self.game, self.root)
        self.assertEqual(owned, {output / "sprites" / "5.png"})

    def test_manifest_cannot_authorize_overwriting_a_non_png_file(self):
        (self.root / "manifest.json").write_text(json.dumps({
            "schemaVersion": 2, "generator": "extract_media.py",
            "sprites": {"not-art": {"file": "notes.txt"}},
        }), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "unsafe"):
            validate_output_directory(self.game, self.root)

    def test_output_links_are_rejected_before_resolution(self):
        linked = self.root / "linked"
        with patch.object(Path, "is_symlink", lambda path: path.name == "linked"):
            with self.assertRaisesRegex(ValueError, "links"):
                validate_output_directory(self.game, linked)

    def test_failed_source_load_cleans_only_new_staging_directory(self):
        before = set(OUTPUT_ROOT.iterdir())
        with self.assertRaises(FileNotFoundError):
            export_media(NS(game_dir=self.game, data_dir=self.game), self.root)
        self.assertEqual(set(OUTPUT_ROOT.iterdir()), before)
        self.assertFalse(any(self.root.iterdir()))

    def test_reexport_preserves_unrelated_files_and_reads_source_only(self):
        game = self.root / "synthetic-game"
        game.mkdir()
        asset_file = game / "sharedassets0.assets"
        asset_file.write_bytes(b"unchanged source")
        output = self.root / "media"
        output.mkdir()
        (output / "sprites").mkdir()
        unrelated = output / "keep.txt"
        unrelated.write_text("untouched", encoding="utf-8")
        generated = output / "sprites" / "sharedassets0.assets-20.png"
        generated.write_bytes(b"previous generated")
        (output / "manifest.json").write_text(json.dumps({
            "schemaVersion": 2, "generator": "extract_media.py",
            "sprites": {"sprite": {"file": "sprites/sharedassets0.assets-20.png"}},
        }), encoding="utf-8")
        fake_assets = NS(
            game_dir=game, data_dir=game, generator=Mock(spec=LocalTypeTreeGenerator),
        )
        fake_exporter = NS(sprites={
            "sprite": {"file": "sprites/sharedassets0.assets-20.png"},
        }, diagnostics=[])
        def render(stage):
            (stage / "sprites").mkdir()
            (stage / "sprites" / "sharedassets0.assets-20.png").write_bytes(b"new generated")
        fake_exporter.export = render
        with (
            patch("extract_media.UnityPy.load", return_value=NS(objects=[])) as load,
            patch("extract_media.SpriteExporter", return_value=fake_exporter),
            patch("extract_media.collect_characters", return_value=({}, {})),
            patch("extract_media.collect_ui", return_value=({}, {}, {})),
        ):
            manifest = export_media(fake_assets, output)
            self.assertEqual(load.call_args.args[0].mode, "rb")
            self.assertTrue(load.call_args.args[0].closed)
        self.assertEqual(asset_file.read_bytes(), b"unchanged source")
        self.assertEqual(unrelated.read_text(encoding="utf-8"), "untouched")
        self.assertEqual(generated.read_bytes(), b"new generated")
        self.assertEqual(json.loads((output / "manifest.json").read_text(encoding="utf-8")), manifest)


class MediaSchemaTests(unittest.TestCase):
    def test_media_schema_is_valid_json_and_versioned(self):
        schema_path = Path(__file__).resolve().parents[1] / "schemas" / "media.schema.json"
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        self.assertEqual(schema["properties"]["schemaVersion"], {"const": 2})
        self.assertEqual(set(schema["properties"]["relationships"]["required"]), {
            "friendly", "enemy", "romance", "business",
        })


@unittest.skipUnless((OUTPUT_ROOT / "media" / "manifest.json").exists(), "Run extract_media.py for local artwork checks")
class LocalMediaExtractionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = OUTPUT_ROOT / "media"
        cls.manifest = json.loads((cls.root / "manifest.json").read_text(encoding="utf-8"))

    def test_nola_and_ava_use_verified_pointer_bindings_and_restored_sizes(self):
        nola = self.manifest["characters"]["Ntt_4AEB60D1"]["portrait"]
        ava = self.manifest["characters"]["Ntt_DC270F89"]["portrait"]
        self.assertEqual(nola["source"]["pathId"], "43570")
        self.assertEqual(ava["source"]["pathId"], "45377")
        self.assertEqual((nola["width"], nola["height"]), (128, 128))
        self.assertEqual((ava["width"], ava["height"]), (256, 256))
        self.assertEqual(nola["restoration"], {
            "left": 0, "top": 1, "decodedWidth": 128, "decodedHeight": 127,
        })
        self.assertEqual(ava["restoration"], {
            "left": 12, "top": 0, "decodedWidth": 244, "decodedHeight": 256,
        })
        self.assertEqual(
            [step["source"]["pathId"] for step in nola["bindings"][0]["via"]],
            ["197837", "206101", "214147"],
        )
        self.assertEqual(ava["bindings"][0]["via"][0]["field"], "overridePortrait")

    def test_relationship_symbols_and_serialized_contextual_tints(self):
        expected = {
            "friendly": ("Friend", "42134", "#3A7540"),
            "enemy": ("Enemy", "41961", "#836041"),
            "romance": ("Romance", "45551", "#A84B4B"),
            "business": ("Business", "42033", "#3A5275"),
        }
        relationships = self.manifest["relationships"]
        self.assertEqual(set(relationships), set(expected))
        for key, (meter, path_id, tint) in expected.items():
            descriptor = relationships[key]
            self.assertEqual((descriptor["meter"], descriptor["source"]["pathId"], descriptor["tint"]),
                             (meter, path_id, tint))
            self.assertEqual((descriptor["width"], descriptor["height"]), (256, 256))
            self.assertEqual(descriptor["tintBinding"]["field"], "m_Color")
        self.assertEqual(relationships["enemy"]["alternateBindings"][0]["tint"], "#CCA269")
        self.assertEqual(relationships["friendly"]["restoration"]["left"], 6)
        self.assertEqual(relationships["friendly"]["restoration"]["top"], 54)

    def test_ui_uses_current_dialogue_binding_not_filename_candidates(self):
        ui = self.manifest["ui"]
        self.assertEqual(ui["dialogueBubble"]["source"]["pathId"], "44725")
        self.assertEqual(ui["dialogueBubble"]["name"], "9S_ChatBubbleExpanded")
        self.assertEqual(ui["dialogueBubble"]["imageType"], 1)
        self.assertAlmostEqual(ui["dialogueBubble"]["pixelsPerUnitMultiplier"], 1.2, places=5)
        self.assertEqual(ui["portraitBorder"]["source"]["pathId"], "41907")
        self.assertEqual(ui["portraitMask"]["source"]["pathId"], "43125")
        self.assertIsNone(self.manifest["provenance"]["portraitImage"]["sprite"])

    def test_canonical_coverage_accounts_for_unresolved_and_conflicting_bindings(self):
        characters = self.manifest["characters"]
        coverage = self.manifest["coverage"]
        self.assertEqual(coverage["canonicalCharacters"], len(characters))
        self.assertEqual(coverage["charactersWithPortrait"],
                         sum(character["portrait"] is not None for character in characters.values()))
        self.assertEqual(coverage["unresolvedCount"], len(coverage["unresolvedCharacters"]))
        self.assertEqual(coverage["conflictCount"], len(coverage["conflicts"]))
        self.assertEqual(coverage["charactersWithPortrait"] + coverage["unresolvedCount"]
                         + coverage["conflictCount"], len(characters))
        self.assertEqual(coverage["charactersWithVariableName"],
                         sum(character["variableName"] is not None for character in characters.values()))
        self.assertEqual(coverage["variableConflictCount"], len(coverage["variableConflicts"]))
        for character in characters.values():
            self.assertIsInstance(character["articyId"], str)
            if character["status"] != "bound":
                self.assertIsNone(character["portrait"])
                self.assertTrue(character["diagnostics"])
        self.assertEqual(characters["Ntt_4AEB60D1"]["variableName"], "NolaBerg")
        self.assertEqual(characters["Ntt_DC270F89"]["variableName"], "Ava")

    def test_all_pngs_are_relative_rgba_original_size_and_sources_are_portable(self):
        for descriptor in self.manifest["sprites"].values():
            path = self.root / descriptor["file"]
            self.assertTrue(path.resolve().is_relative_to(self.root.resolve()))
            self.assertIsInstance(descriptor["source"]["pathId"], str)
            self.assertNotIn("/", descriptor["source"]["file"])
            self.assertNotIn("\\", descriptor["source"]["file"])
            with Image.open(path) as image:
                self.assertEqual(image.mode, "RGBA")
                self.assertEqual(image.size, (descriptor["width"], descriptor["height"]))
        serialized = json.dumps(self.manifest)
        self.assertNotIn(r"C:\\Users", serialized)
        self.assertNotIn("Program Files", serialized)


if __name__ == "__main__":
    unittest.main()
