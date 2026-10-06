import unittest

from extract_dialogue import enumerate_paths, find_localized_dialogue, lossless, parse_effects


class DialogueTests(unittest.TestCase):
    def test_literal_effects_and_unknown_scripts(self):
        self.assertEqual(parse_effects(
            "// Shared reward\nNpc.Friend += 1; Npc.Enemy -= 2; Npc.Met = true; custom(42)"
        ), [
            {"type": "delta", "variable": "Npc.Friend", "value": 1},
            {"type": "delta", "variable": "Npc.Enemy", "value": -2},
            {"type": "set", "variable": "Npc.Met", "value": True},
            {"type": "unparsed", "script": "custom(42)"},
        ])

    def test_boolean_increment_is_not_interpreted(self):
        self.assertEqual(parse_effects("Npc.Friend += true"), [
            {"type": "unparsed", "script": "Npc.Friend += true"}
        ])

    def test_uint64_ids_survive_javascript_json(self):
        self.assertEqual(lossless({"id": 72057714297016594, "items": [1, True]}),
                         {"id": "72057714297016594", "items": [1, True]})

    def test_localization_matches_guid_not_array_position(self):
        expected = {"lines": [{"GUID": "a"}, {"GUID": "b"}]}
        root = [{"dialogues": [{"lines": [{"GUID": "unrelated"}]}, expected]}]
        self.assertIs(find_localized_dialogue(root, {"a", "b"}), expected)

    def test_ambiguous_localization_fails(self):
        with self.assertRaisesRegex(ValueError, "found 2"):
            find_localized_dialogue([{"lines": [{"GUID": "a"}]}] * 2, {"a"})

    @staticmethod
    def graph():
        def node(identifier, kind="action", effects=None):
            return {"id": identifier, "type": kind, "effects": effects or [], "conditions": []}

        def edge(source, target, effects=None, conditions=None):
            return {
                "from": source, "to": target, "effects": effects or [],
                "conditions": conditions or [],
            }

        return {
            "entry": "entry", "exit": "exit",
            "nodes": [
                node("entry", "entry"), node("a", "choice"), node("b", "choice"),
                node("reward", effects=[{"type": "unlock_recipe", "recipe": "Soup"}]),
                node("exit", "exit"), node("external", "external"),
            ],
            "edges": [
                edge("entry", "a", [{"type": "delta", "variable": "Npc.Friend", "value": 1}]),
                edge("entry", "b", conditions=["Npc.Friend >= 5"]),
                edge("a", "reward"), edge("b", "reward"),
                edge("reward", "exit"),
                edge("exit", "external", [{"type": "delta", "variable": "Npc.Friend", "value": 99}]),
            ],
        }

    def test_branches_rejoin_reward_and_stop_at_exit(self):
        paths = enumerate_paths(self.graph())
        self.assertEqual(len(paths), 2)
        self.assertEqual(paths[0]["summary"]["deltas"], {"Npc.Friend": 1})
        self.assertEqual(paths[1]["summary"]["deltas"], {})
        self.assertTrue(all(path["summary"]["recipes"] == ["Soup"] for path in paths))
        self.assertEqual(paths[1]["conditions"], ["Npc.Friend >= 5"])
        self.assertTrue(all("external" not in path["nodes"] for path in paths))

    def test_dead_end_is_not_mistaken_for_completion(self):
        graph = self.graph()
        graph["edges"] = [edge for edge in graph["edges"] if edge["from"] != "reward"]
        with self.assertRaisesRegex(ValueError, "Unexpected dead end"):
            enumerate_paths(graph)

    def test_cycles_are_explicit(self):
        graph = self.graph()
        graph["edges"].append({"from": "reward", "to": "a", "effects": [], "conditions": []})
        with self.assertRaisesRegex(ValueError, "Cycle encountered"):
            enumerate_paths(graph)


if __name__ == "__main__":
    unittest.main()
