import json
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace

from export_guide import collect_quest, filename, quest_links, validate_pack, write_json
from extract_dialogue import LocalizationIndex, RouteEnumerationError, enumerate_paths, template_action
from outcome_analysis import analyze_effect_outcomes
import test_dialogue


class GuideExportTests(unittest.TestCase):
    def test_unsafe_ids_get_safe_stable_filenames(self):
        self.assertEqual(filename("Noodle_Bar"), "Noodle_Bar.json")
        unsafe = filename("../../not-a-path")
        self.assertNotIn("/", unsafe)
        self.assertNotIn("\\", unsafe)
        self.assertEqual(unsafe, filename("../../not-a-path"))

    def test_links_are_explicit_and_many_to_many(self):
        quests = [
            {"id": "q1", "dialogueIds": ["dialogue"], "objectives": [
                {"id": "o1", "dialogueIds": ["dialogue"]}]},
            {"id": "q2", "dialogueIds": ["dialogue"], "objectives": [
                {"id": "o2", "dialogueIds": ["dialogue"]}]},
        ]
        quest_map, objective_map = quest_links(quests)
        self.assertEqual(quest_map["dialogue"], {"q1", "q2"})
        self.assertEqual(objective_map["dialogue"], {"o1", "o2"})
        self.assertEqual(quest_map["unrelated"], set())

    def test_item_selector_becomes_a_readable_data_defined_action(self):
        assets = SimpleNamespace(read=lambda identifier: {
            "technicalName": "supplies", "mDisplayName": "Supplies"
        })
        effect = template_action(assets, "GivePlayerItemTmpl", {
            "mItemSelector": {"value": {"mItem": {"objectRef": 42}, "mAmount": 3.0}}
        })
        self.assertEqual(effect["label"], "Receive 3 x Supplies")
        self.assertEqual(effect["referenceId"], "supplies")
        self.assertEqual(effect["type"], "template_action")

    def test_renamed_quest_and_non_dialogue_objective(self):
        empty_script = {"value": {"mRawScript": ""}}
        record = {
            "technicalName": "intro", "mDisplayName": "Draft title", "mText": "Draft",
            "childrenIds": [2], "mInputPins": {"value": []}, "mOutputPins": {"value": []},
            "mTemplate": {"value": {"mObjectivesDisplay": {"value": {
                "mOwner": {"objectRef": 0}, "mOnStart": empty_script,
                "mOnComplete": {"value": {"mRawScript": "Player.IntroComplete = true"}},
            }}}},
        }
        objective = {
            "technicalName": "acquire", "mDisplayName": "Acquire supplies",
            "mText": "", "mTemplate": {"value": {"mObjectiveBase": {"value": {
                "mTargetCharacterOrSpot": {"objectRef": 0},
                "mOnStart": empty_script, "mOnComplete": empty_script,
                "mCountVariableName": "Inventory.Supplies", "mTargetCount": 3,
            }}}},
        }
        records = {1: record, 2: objective}
        assets = SimpleNamespace(
            read=lambda identifier: records[identifier],
            class_name=lambda identifier: {1: "QuestFlow", 2: "Acquire_Items_Objective"}[identifier],
            source=lambda identifier: {"pathId": str(identifier)},
        )
        locale = SimpleNamespace(records={
            "intro": {"name": "Getting started", "description": "A new beginning."}
        })
        quest = collect_quest(assets, locale, 1)
        self.assertEqual(quest["title"], "Getting started")
        self.assertEqual(quest["dialogueIds"], [])
        self.assertEqual(quest["objectives"][0]["dialogueIds"], [])
        self.assertEqual(quest["objectives"][0]["targetCount"], 3)
        self.assertEqual(quest["onComplete"][0]["variable"], "Player.IntroComplete")

    def test_route_limits_are_not_reported_as_complete(self):
        with self.assertRaises(RouteEnumerationError) as context:
            enumerate_paths(test_dialogue.DialogueTests.graph(), max_paths=1)
        self.assertEqual(context.exception.status, "limit")

    def test_localization_missing_is_explicit(self):
        locale = LocalizationIndex({"source": {}, "raw": [
            {"GUID": "a", "TextWPronouns": {"TextHeVersion": "Hello"}, "ChoicesWPronouns": []}
        ]})
        with self.assertRaisesRegex(ValueError, "Missing localized"):
            locale.select({"missing"})
        self.assertEqual(locale.select({"a", "missing"}, strict=False)["missingGuids"], ["missing"])

    def test_catalog_path_cannot_escape_pack(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            write_json(root / "catalog.json", {
                "schemaVersion": 2, "defaultQuestId": None, "defaultDialogueId": "d",
                "quests": [], "dialogues": [{"id": "d", "file": "../outside.json"}],
            })
            with self.assertRaisesRegex(ValueError, "escapes"):
                validate_pack(root)

    def write_effect_pack(self, root):
        graph = test_dialogue.DialogueTests.graph()
        graph.update(schemaVersion=2, dialogueId="d", questIds=[], objectiveIds=[])
        for index, edge in enumerate(graph["edges"]):
            edge["id"] = f"edge-{index}"
        graph["effectAnalysis"] = analyze_effect_outcomes(graph)
        write_json(root / "catalog.json", {
            "schemaVersion": 2, "defaultQuestId": None, "defaultDialogueId": "d",
            "quests": [], "dialogues": [{"id": "d", "file": "dialogues/d.json"}],
            "searchFile": "search/dialogues.json",
        })
        write_json(root / "dialogues" / "d.json", graph)
        index = {"schemaVersion": 2, "dialogues": [{
            "id": "d", "title": "Example", "speakers": [], "lines": [["a", "choice", None, "Reply"]],
        }]}
        write_json(root / "search" / "dialogues.json", index)
        return graph, index

    def test_search_and_witness_references_are_validated(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            self.write_effect_pack(root)
            validate_pack(root)

    def test_disconnected_effect_witness_is_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            graph, _ = self.write_effect_pack(root)
            graph["effectAnalysis"]["outcomes"][0]["witness"]["edges"][0] = "edge-4"
            write_json(root / "dialogues" / "d.json", graph)
            with self.assertRaisesRegex(ValueError, "Disconnected"):
                validate_pack(root)

    def test_unknown_search_node_is_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            _, index = self.write_effect_pack(root)
            index["dialogues"][0]["lines"][0][0] = "not-in-graph"
            write_json(root / "search" / "dialogues.json", index)
            with self.assertRaisesRegex(ValueError, "unknown spoken node"):
                validate_pack(root)

    def test_search_catalog_mismatch_is_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            _, index = self.write_effect_pack(root)
            index["dialogues"][0]["id"] = "another-dialogue"
            write_json(root / "search" / "dialogues.json", index)
            with self.assertRaisesRegex(ValueError, "index/catalog mismatch"):
                validate_pack(root)

    def test_media_paths_must_exist_inside_the_pack(self):
        cases = [
            ("relationships", "friendly", "../../../outside.png", "escapes"),
            ("relationships", "friendly", "missing.png", "Missing artwork"),
            ("ui", "dialogueBubble", "../../../outside.png", "escapes"),
        ]
        for collection, role, file, expected in cases:
            with self.subTest(collection=collection, file=file), tempfile.TemporaryDirectory() as folder:
                root = Path(folder)
                write_json(root / "catalog.json", {
                    "schemaVersion": 2, "defaultQuestId": None, "defaultDialogueId": None,
                    "quests": [], "dialogues": [], "mediaFile": "media/manifest.json",
                })
                manifest = {
                    "schemaVersion": 2, "characters": {},
                    "relationships": {}, "ui": {},
                }
                manifest[collection][role] = {"file": file}
                write_json(root / "media" / "manifest.json", manifest)
                with self.assertRaisesRegex(ValueError, expected):
                    validate_pack(root)

    def test_schema_files_are_valid_json_and_versioned(self):
        root = Path(__file__).resolve().parents[1]
        for path in (root / "schemas").glob("*.schema.json"):
            schema = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(schema["properties"]["schemaVersion"]["const"], 2)


class LocalGuidePackTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = Path(__file__).resolve().parents[1] / "output" / "data"
        if not (cls.root / "catalog.json").exists():
            raise unittest.SkipTest("Generate a local guide pack first")
        cls.catalog = validate_pack(cls.root)

    def test_nola_objective_links_to_actual_dialogue(self):
        entry = next(item for item in self.catalog["quests"] if item["id"] == "Noodle_Bar")
        quest = json.loads((self.root / entry["file"]).read_text(encoding="utf-8"))
        objective = next(item for item in quest["objectives"] if item["id"] == "Dlg_2D4D0818")
        self.assertEqual(objective["title"], "Get Recipe Tips from Nola Berg")
        self.assertEqual(objective["targetName"], "Nola Berg")
        self.assertEqual(objective["dialogueIds"], ["Dlg_2FF9F477"])
        self.assertIn("Dlg_2FF9F477", quest["dialogueIds"])

    def test_all_graph_values_are_web_safe(self):
        for entry in self.catalog["dialogues"]:
            graph = json.loads((self.root / entry["file"]).read_text(encoding="utf-8"))
            self.assertNotIn("gameDirectory", graph["provenance"])
            for node in graph["nodes"]:
                self.assertIsInstance(node["text"], str)
                if "articyId" in node:
                    self.assertIsInstance(node["articyId"], str)
            if graph["analysis"]["status"] != "complete":
                self.assertTrue(graph["warnings"])


if __name__ == "__main__":
    unittest.main()
