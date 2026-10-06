import json
from pathlib import Path
import unittest


OUTPUT = Path(__file__).resolve().parents[1] / "output" / "data" / "dialogues" / "Dlg_2FF9F477.json"


@unittest.skipUnless(OUTPUT.exists(), "Run the extractor to check the locally generated Nola graph")
class NolaExtractionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.graph = json.loads(OUTPUT.read_text(encoding="utf-8"))
        cls.nodes = {node["id"]: node for node in cls.graph["nodes"]}

    def test_observed_gameplay_route(self):
        choices = [
            "I just took over the Noodle Bar.",
            "I'm looking for recipe tips.",
            "How should I ask?",
            "That's great advice, thanks.",
            "You'll help me?",
            "We're all in this together.",
        ]
        matches = [
            path for path in self.graph["paths"]
            if [self.nodes[identifier]["text"] for identifier in path["choices"]] == choices
        ]
        self.assertEqual(len(matches), 1)
        summary = matches[0]["summary"]
        self.assertEqual(summary["deltas"], {
            "NolaBerg.Business": 1, "NolaBerg.Friend": 2, "Banor.RecipeRun": 1
        })
        self.assertEqual(summary["assignments"], {"NolaBerg.Met": True})
        self.assertEqual(summary["recipes"], ["Beer"])

    def test_all_routes_and_distinct_relationship_outcomes(self):
        self.assertEqual(len(self.graph["paths"]), 12)
        outcomes = {
            (path["summary"]["deltas"].get("NolaBerg.Friend", 0),
             path["summary"]["deltas"].get("NolaBerg.Business", 0))
            for path in self.graph["paths"]
        }
        self.assertEqual(outcomes, {(0, 2), (1, 1), (1, 2), (2, 1)})
        for path in self.graph["paths"]:
            self.assertEqual(path["summary"]["recipes"], ["Beer"])
            self.assertEqual(path["summary"]["deltas"]["Banor.RecipeRun"], 1)
            self.assertEqual(path["summary"]["unparsed"], [])
            self.assertEqual(path["conditions"], [])
            self.assertNotIn("NolaBerg.Enemy", path["summary"]["deltas"])
            self.assertNotIn("NolaBerg.Romance", path["summary"]["deltas"])

    def test_external_gate_does_not_become_an_internal_condition(self):
        exits = [edge for edge in self.graph["edges"] if edge["from"] == self.graph["exit"]]
        self.assertEqual(len(exits), 1)
        self.assertEqual(exits[0]["conditions"], ["Banor.RecipeRun >= 3"])
        self.assertEqual(self.nodes[exits[0]["to"]]["type"], "external")

    def test_source_ids_and_reward_template_are_preserved(self):
        reward = self.nodes["Ins_38D0124E"]
        self.assertEqual(reward["className"], "UnlockRecipe")
        unlock = next(effect for effect in reward["effects"] if effect["type"] == "unlock_recipe")
        self.assertEqual(unlock["articyId"], "72057787311456400")
        for node in self.graph["nodes"]:
            if "articyId" in node:
                self.assertIsInstance(node["articyId"], str)
                self.assertIn("pathId", node["source"])
                if "raw" in node:
                    self.assertEqual(node["raw"]["id"], node["articyId"])
                    self.assertEqual(node["raw"]["m_Script"]["m_FileID"], 1)


if __name__ == "__main__":
    unittest.main()
