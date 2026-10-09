import copy
import json
from pathlib import Path
import unittest

from outcome_analysis import analyze_effect_outcomes


OUTPUT = Path(__file__).resolve().parents[1] / "output" / "data" / "dialogues"


def node(identifier, kind="line", effects=(), conditions=()):
    return {"id": identifier, "type": kind, "effects": list(effects), "conditions": list(conditions)}


def edge(source, target, effects=(), conditions=(), identifier=None):
    return {
        "id": identifier or source + "->" + target, "from": source, "to": target,
        "effects": list(effects), "conditions": list(conditions),
    }


def delta(value, variable="Npc.Friend"):
    return {"type": "delta", "variable": variable, "value": value}


def assign(value, variable="Npc.Friend"):
    return {"type": "set", "variable": variable, "value": value}


def graph(nodes, edges):
    return {
        "schemaVersion": 2, "entry": "entry", "exit": "exit",
        "nodes": [node("entry", "entry"), *nodes, node("exit", "exit")],
        "edges": edges,
    }


class OutcomeAnalysisTests(unittest.TestCase):
    def assert_witnesses(self, source, result):
        nodes = {item["id"]: item for item in source["nodes"]}
        edges = {item["id"]: item for item in source["edges"]}
        for outcome in result["outcomes"]:
            witness = outcome["witness"]
            self.assertEqual(witness["nodes"][0], source["entry"])
            self.assertEqual(witness["nodes"][-1], source["exit"])
            self.assertEqual(len(witness["edges"]), len(witness["nodes"]) - 1)
            effects = []
            for index, identifier in enumerate(witness["nodes"]):
                effects.extend(nodes[identifier]["effects"])
                if index < len(witness["edges"]):
                    transition = edges[witness["edges"][index]]
                    self.assertEqual(transition["from"], identifier)
                    self.assertEqual(transition["to"], witness["nodes"][index + 1])
                    self.assertNotEqual(identifier, source["exit"])
                    self.assertNotIn(nodes[identifier]["type"], {"external", "subdialogue"})
                    effects.extend(transition["effects"])
            self.assertEqual(outcome["effects"], effects)
            self.assertEqual(witness["choices"], [
                identifier for identifier in witness["nodes"] if nodes[identifier]["type"] == "choice"
            ])

    def test_exponential_wording_branches_merge_without_raw_paths(self):
        nodes, edges, previous = [], [], "entry"
        for index in range(60):
            left, right, join = f"left{index}", f"right{index}", f"join{index}"
            nodes.extend([node(left, "choice"), node(right, "choice"), node(join)])
            edges.extend([
                edge(previous, left), edge(previous, right),
                edge(left, join), edge(right, join),
            ])
            previous = join
        edges.append(edge(previous, "exit", [delta(2)]))
        source = graph(nodes, edges)
        source["paths"] = []
        source["analysis"] = {"status": "limit"}
        before = copy.deepcopy(source)
        result = analyze_effect_outcomes(source, max_states=200)
        self.assertEqual(source, before)
        self.assertEqual(result["status"], "complete")
        self.assertEqual(len(result["outcomes"]), 1)
        self.assertEqual(result["outcomes"][0]["summary"]["deltas"], {"Npc.Friend": 2})
        self.assertEqual(result["stats"]["mergedStates"], 60)
        self.assert_witnesses(source, result)

    def test_mutually_exclusive_bonuses_and_effectful_edges(self):
        source = graph([node("friend", "choice"), node("business", "choice")], [
            edge("entry", "friend", [delta(2)]),
            edge("entry", "business", [delta(3, "Npc.Business")]),
            edge("friend", "exit"), edge("business", "exit"),
            edge("exit", "friend", [delta(99)]),
        ])
        result = analyze_effect_outcomes(source)
        self.assertEqual(result["status"], "complete")
        self.assertEqual({
            tuple(sorted(outcome["summary"]["deltas"].items())) for outcome in result["outcomes"]
        }, {(("Npc.Friend", 2),), (("Npc.Business", 3),)})
        self.assertEqual(result["stats"]["effectBearingEdges"], 3)
        self.assert_witnesses(source, result)

    def test_ordered_sets_deltas_negative_values_and_boolean_types(self):
        source = graph([
            node("a", effects=[assign(3), delta(2), assign(True, "Npc.Met")]),
            node("b", effects=[delta(2), assign(3), assign(False, "Npc.Met")]),
            node("c", effects=[delta(-2), delta(-3)]),
            node("d", effects=[assign(1)]),
            node("e", effects=[assign(True)]),
        ], [
            transition for name in "abcde"
            for transition in [edge("entry", name), edge(name, "exit")]
        ])
        result = analyze_effect_outcomes(source)
        self.assertEqual(result["status"], "complete")
        self.assertEqual(len(result["outcomes"]), 5)
        by_node = {outcome["witness"]["nodes"][1]: outcome["summary"] for outcome in result["outcomes"]}
        self.assertEqual(by_node["a"]["assignments"], {"Npc.Friend": 5, "Npc.Met": True})
        self.assertEqual(by_node["b"]["assignments"], {"Npc.Friend": 3, "Npc.Met": False})
        self.assertEqual(by_node["a"]["deltas"], {})
        self.assertEqual(by_node["c"]["deltas"], {"Npc.Friend": -5})
        self.assertIs(type(by_node["d"]["assignments"]["Npc.Friend"]), int)
        self.assertIs(type(by_node["e"]["assignments"]["Npc.Friend"]), bool)
        self.assert_witnesses(source, result)

    def test_reset_overrides_delta_and_cancelling_deltas_merge(self):
        source = graph([
            node("a", effects=[delta(7), assign(3), delta(-2)]),
            node("b", effects=[assign(1)]),
            node("c", effects=[delta(1), delta(-1)]),
            node("d"),
        ], [
            transition for name in "abcd"
            for transition in [edge("entry", name), edge(name, "exit")]
        ])
        result = analyze_effect_outcomes(source)
        self.assertEqual(result["status"], "complete")
        self.assertEqual(len(result["outcomes"]), 2)
        self.assertEqual({json.dumps(outcome["summary"]["assignments"]) for outcome in result["outcomes"]},
                         {'{"Npc.Friend": 1}', '{}'})

    def test_neutral_guards_survive_and_formatting_duplicates_normalize(self):
        source = graph([
            node("a", conditions=["Npc.Friend >= 5", 'Name == "a b"']),
            node("b", conditions=["Npc.Friend>=5", 'Name=="a b"']),
            node("c", conditions=["Npc.Business > 3"]),
        ], [
            transition for name in "abc"
            for transition in [
                edge("entry", name),
                edge(name, "exit", conditions=["  Npc.Friend >= 5  "]),
            ]
        ])
        result = analyze_effect_outcomes(source)
        self.assertEqual(result["status"], "complete")
        self.assertEqual(len(result["outcomes"]), 2)
        for outcome in result["outcomes"]:
            self.assertEqual(len(outcome["conditions"]), 2)
        self.assert_witnesses(source, result)

    def test_conditions_inside_quoted_strings_are_not_deduplicated(self):
        source = graph([node("a"), node("b")], [
            edge("entry", "a", conditions=['Name == "a b"']),
            edge("entry", "b", conditions=['Name == "ab"']),
            edge("a", "exit"), edge("b", "exit"),
        ])
        self.assertEqual(len(analyze_effect_outcomes(source)["outcomes"]), 2)

    def test_distinct_operator_tokens_are_not_deduplicated(self):
        source = graph([node("a"), node("b")], [
            edge("entry", "a", conditions=["x++ > 1"]),
            edge("entry", "b", conditions=["x + + 1 > 1"]),
            edge("a", "exit"), edge("b", "exit"),
        ])
        self.assertEqual(len(analyze_effect_outcomes(source)["outcomes"]), 2)

    def test_empty_conversation_and_exit_are_terminal(self):
        source = graph([node("outside", "external")], [
            edge("entry", "exit"),
            edge("exit", "outside", [delta(99)], ["Gate"]),
        ])
        result = analyze_effect_outcomes(source)
        self.assertEqual(result["status"], "complete")
        self.assertEqual(result["outcomes"][0]["conditions"], [])
        self.assertEqual(result["outcomes"][0]["summary"], {
            "deltas": {}, "assignments": {}, "recipes": [], "actions": [], "unparsed": [],
        })
        self.assert_witnesses(source, result)

    def test_boundaries_do_not_produce_fake_outcomes_even_with_outgoing_edges(self):
        for kind in ("external", "subdialogue"):
            with self.subTest(kind=kind):
                source = graph([node("outside", kind, [delta(9)])], [
                    edge("entry", "outside"), edge("outside", "exit"),
                ])
                result = analyze_effect_outcomes(source)
                self.assertEqual(result["status"], "partial")
                self.assertEqual(result["outcomes"], [])
                self.assertEqual({item["kind"] for item in result["diagnostics"]},
                                 {"boundary", "unreachable_exit"})

    def test_dead_end_is_not_completion_and_valid_empty_route_survives(self):
        source = graph([node("dead")], [edge("entry", "dead"), edge("entry", "exit")])
        result = analyze_effect_outcomes(source)
        self.assertEqual(result["status"], "partial")
        self.assertEqual(len(result["outcomes"]), 1)
        self.assertTrue(any(item["kind"] == "dead_end" for item in result["diagnostics"]))
        self.assert_witnesses(source, result)
        source["edges"].pop()
        self.assertEqual(analyze_effect_outcomes(source)["outcomes"], [])

    def test_neutral_and_idempotent_cycles_converge_with_guard_evidence(self):
        for effects in ([], [assign(True, "Npc.Met"), {"type": "unlock_recipe", "recipe": "Soup"}]):
            with self.subTest(effects=effects):
                source = graph([
                    node("loop", effects=effects), node("gate", conditions=["Npc.Met"]),
                ], [
                    edge("entry", "loop"), edge("loop", "gate"),
                    edge("gate", "loop"), edge("loop", "exit"),
                ])
                result = analyze_effect_outcomes(source)
                self.assertEqual(result["status"], "complete")
                self.assertEqual(len(result["outcomes"]), 2)
                self.assertEqual({tuple(item["conditions"]) for item in result["outcomes"]},
                                 {(), ("Npc.Met",)})
                self.assertLess(result["stats"]["discoveredStates"], 10)
                self.assert_witnesses(source, result)

    def test_positive_negative_and_unknown_action_cycles_are_explicit(self):
        for effects in ([delta(1)], [delta(-1)],
                        [{"type": "unparsed", "script": "maybe_reward()"}],
                        [{"type": "template_action", "label": "Receive 2 x Soup", "quantity": 2}]):
            with self.subTest(effects=effects):
                source = graph([node("loop", effects=effects)], [
                    edge("entry", "loop"), edge("loop", "loop"), edge("loop", "exit"),
                ])
                result = analyze_effect_outcomes(source)
                self.assertEqual(result["status"], "cycle")
                self.assertFalse(result["complete"])
                self.assertEqual(len(result["outcomes"]), 1)
                self.assertEqual(result["stats"]["omittedCycleTransitions"], 1)
                self.assertEqual(result["outcomes"][0]["effects"], effects)
                self.assert_witnesses(source, result)

    def test_effectful_edge_cycle_cannot_become_an_empty_completed_route(self):
        source = graph([node("loop")], [
            edge("entry", "loop"), edge("loop", "loop", [delta(1)]),
        ])
        result = analyze_effect_outcomes(source)
        self.assertEqual(result["status"], "cycle")
        self.assertEqual(result["outcomes"], [])
        self.assertTrue(any(item["kind"] == "unreachable_exit" for item in result["diagnostics"]))

    def test_omitted_loop_script_and_guards_remain_in_cycle_diagnostics(self):
        unknown = {"type": "unparsed", "script": "unknown_reward()"}
        source = graph([node("loop")], [
            edge("entry", "loop"), edge("loop", "loop", [unknown], ["CanRepeat"]),
            edge("loop", "exit"),
        ])
        result = analyze_effect_outcomes(source)
        self.assertEqual(result["status"], "cycle")
        diagnostic = next(item for item in result["diagnostics"] if item["kind"] == "effectful_cycle")
        self.assertEqual(diagnostic["conditions"], ["CanRepeat"])
        self.assertEqual(diagnostic["effects"], [{"edge": "loop->loop", "effects": [unknown]}])
        self.assertEqual(result["outcomes"][0]["effects"], [])
        self.assert_witnesses(source, result)

    def test_cyclic_visited_sets_do_not_hide_different_valid_exit_routes(self):
        source = graph([node("a", effects=[delta(1)]), node("b")], [
            edge("entry", "a"), edge("entry", "b"),
            edge("a", "b"), edge("b", "a"), edge("a", "exit"),
        ])
        result = analyze_effect_outcomes(source)
        self.assertEqual(result["status"], "cycle")
        self.assertEqual(len(result["outcomes"]), 1)
        self.assert_witnesses(source, result)

    def test_opaque_order_and_repeated_action_quantities_are_retained(self):
        script = {"type": "unparsed", "script": "custom()"}
        action = {
            "type": "template_action", "action": "GivePlayerItemTmpl",
            "label": "Receive 2 x Soup", "quantity": 2, "referenceId": "Soup",
        }
        source = graph([
            node("a", effects=[assign(3), script, delta(2), action, action]),
            node("b", effects=[script, assign(3), delta(2), action]),
        ], [
            edge("entry", "a"), edge("entry", "b"), edge("a", "exit"), edge("b", "exit"),
        ])
        result = analyze_effect_outcomes(source)
        self.assertEqual(result["status"], "partial")
        self.assertEqual(len(result["outcomes"]), 2)
        self.assertEqual(sorted(len(item["summary"]["actions"]) for item in result["outcomes"]), [1, 2])
        self.assertTrue(all(item["summary"]["unparsed"] == ["custom()"] for item in result["outcomes"]))
        self.assert_witnesses(source, result)
        source["nodes"][1]["effects"].pop()
        self.assertEqual(len(analyze_effect_outcomes(source)["outcomes"]), 2)

    def test_unsupported_kinds_and_boolean_delta_remain_uncertain(self):
        source = graph([node("a", effects=[
            assign(True), delta(1), {"type": "mystery", "quantity": 3},
        ])], [edge("entry", "a"), edge("a", "exit")])
        result = analyze_effect_outcomes(source)
        self.assertEqual(result["status"], "partial")
        self.assertEqual(len(result["outcomes"][0]["summary"]["unparsed"]), 2)
        self.assertIs(result["outcomes"][0]["summary"]["assignments"]["Npc.Friend"], True)
        self.assert_witnesses(source, result)

    def test_zero_delta_on_boolean_cycle_is_not_claimed_to_converge(self):
        source = graph([node("flag", effects=[assign(True)]), node("loop", effects=[delta(0)])], [
            edge("entry", "flag"), edge("flag", "loop"),
            edge("loop", "loop"), edge("loop", "exit"),
        ])
        result = analyze_effect_outcomes(source)
        self.assertEqual(result["status"], "cycle")
        self.assertEqual(len(result["outcomes"]), 1)
        self.assertEqual(len(result["outcomes"][0]["summary"]["unparsed"]), 1)
        self.assert_witnesses(source, result)

    def test_numeric_overflow_is_opaque_instead_of_invalid_json(self):
        source = graph([node("a", effects=[assign(1e308), delta(1e308)])], [
            edge("entry", "a"), edge("a", "exit"),
        ])
        result = analyze_effect_outcomes(source)
        self.assertEqual(result["status"], "partial")
        self.assertEqual(len(result["outcomes"][0]["summary"]["unparsed"]), 1)
        json.dumps(result, allow_nan=False)

    def test_budgets_are_explicit_and_keep_only_genuine_found_witnesses(self):
        source = graph([node("a"), node("b")], [
            edge("entry", "exit"), edge("entry", "a"), edge("entry", "b"),
        ])
        result = analyze_effect_outcomes(source, max_states=2)
        self.assertEqual(result["status"], "limit")
        self.assertEqual(result["stats"]["discoveredStates"], 2)
        self.assertEqual(len(result["outcomes"]), 1)
        self.assert_witnesses(source, result)
        for invalid in (0, -1, True, 1.5):
            with self.assertRaises(ValueError):
                analyze_effect_outcomes(source, max_states=invalid)
        source = graph([], [
            edge("entry", "exit", [delta(1)] * 129),
        ])
        result = analyze_effect_outcomes(source, max_states=1)
        self.assertEqual(result["status"], "limit")
        self.assertEqual(result["stats"]["effectOperations"], 128)
        self.assertEqual(result["outcomes"], [])

    def test_transition_budget_bounds_parallel_neutral_edges(self):
        source = graph([], [
            edge("entry", "exit", identifier=f"parallel-{index}") for index in range(100)
        ])
        result = analyze_effect_outcomes(source, max_states=2)
        self.assertEqual(result["status"], "limit")
        self.assertEqual(result["stats"]["processedTransitions"], 64)
        self.assertEqual(len(result["outcomes"]), 1)
        self.assert_witnesses(source, result)

    def test_capped_branching_still_finds_an_actual_exit_outcome(self):
        source = graph(
            [node(f"n{index}") for index in range(25)] + [node("reward", effects=[delta(1)])],
            [
                *[edge("entry", f"n{index}") for index in range(25)],
                *[edge(f"n{index}", "reward", conditions=[f"Flag{index}"]) for index in range(25)],
                edge("reward", "exit"),
            ],
        )
        result = analyze_effect_outcomes(source, max_states=30)
        self.assertEqual(result["status"], "limit")
        self.assertGreater(len(result["outcomes"]), 0)
        self.assert_witnesses(source, result)

    def test_variable_state_size_is_bounded_on_long_effectful_chains(self):
        source = graph([
            node(f"n{index}", effects=[assign(index, f"Var{index}")]) for index in range(150)
        ], [
            edge("entry", "n0"),
            *[edge(f"n{index}", f"n{index + 1}") for index in range(149)],
            edge("n149", "exit"),
        ])
        result = analyze_effect_outcomes(source, max_states=200)
        self.assertEqual(result["status"], "complete")
        result = analyze_effect_outcomes(source, max_states=151)
        self.assertEqual(result["status"], "limit")
        self.assertLessEqual(result["stats"]["stateUnits"], result["stats"]["stateUnitLimit"])
        self.assertTrue(any("size budget" in item["message"] for item in result["diagnostics"]))

    def test_witness_output_budget_prevents_quadratic_output_growth(self):
        nodes = [node(f"n{index}") for index in range(200)]
        nodes.extend(node(f"choice{index}", effects=[delta(index + 1)]) for index in range(150))
        source = graph(nodes, [
            edge("entry", "n0"),
            *[edge(f"n{index}", f"n{index + 1}") for index in range(199)],
            *[edge("n199", f"choice{index}") for index in range(150)],
            *[edge(f"choice{index}", "exit") for index in range(150)],
        ])
        result = analyze_effect_outcomes(source, max_states=600)
        self.assertEqual(result["status"], "complete")
        result = analyze_effect_outcomes(source, max_states=501)
        self.assertEqual(result["status"], "complete")
        # Add a longer common prefix: states still fit, but copied witnesses do not.
        source["nodes"].extend(node(f"prefix{index}") for index in range(200))
        source["edges"][0] = edge("entry", "prefix0")
        source["edges"].extend([
            *[edge(f"prefix{index}", f"prefix{index + 1}") for index in range(199)],
            edge("prefix199", "n0"),
        ])
        result = analyze_effect_outcomes(source, max_states=750)
        self.assertEqual(result["status"], "limit")
        self.assertGreater(len(result["outcomes"]), 0)
        self.assertLessEqual(result["stats"]["witnessSteps"], result["stats"]["witnessStepLimit"])
        self.assertTrue(any("Witness-output" in item["message"] for item in result["diagnostics"]))
        self.assert_witnesses(source, result)

    def test_long_graphs_do_not_recurse_and_ids_are_stable(self):
        source = graph([node(str(index)) for index in range(1500)], [
            edge("entry", "0"),
            *[edge(str(index), str(index + 1)) for index in range(1499)],
            edge("1499", "exit"),
        ])
        result = analyze_effect_outcomes(source)
        self.assertEqual(result["status"], "complete")
        self.assert_witnesses(source, result)
        source["nodes"].reverse()
        source["edges"].reverse()
        repeated = analyze_effect_outcomes(source)
        self.assertEqual(result["outcomes"][0]["id"], repeated["outcomes"][0]["id"])

    def test_missing_real_edge_id_and_unknown_target_are_partial(self):
        source = graph([], [edge("entry", "missing"), edge("entry", "exit")])
        source["edges"][1].pop("id")
        result = analyze_effect_outcomes(source)
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["outcomes"], [])
        self.assertEqual(sum(item["kind"] == "invalid_transition" for item in result["diagnostics"]), 2)

    @unittest.skipUnless((OUTPUT / "Dlg_2FF9F477.json").exists(), "Generated Nola graph is optional")
    def test_generated_nola_has_four_compatible_beer_outcomes(self):
        source = json.loads((OUTPUT / "Dlg_2FF9F477.json").read_text(encoding="utf-8"))
        result = analyze_effect_outcomes(source)
        self.assertEqual(result["status"], "complete")
        self.assertEqual(len(result["outcomes"]), 4)
        self.assertEqual({
            (item["summary"]["deltas"].get("NolaBerg.Friend", 0),
             item["summary"]["deltas"].get("NolaBerg.Business", 0))
            for item in result["outcomes"]
        }, {(0, 2), (1, 1), (1, 2), (2, 1)})
        for item in result["outcomes"]:
            self.assertEqual(item["summary"]["recipes"], ["Beer"])
            self.assertEqual(item["summary"]["assignments"], {"NolaBerg.Met": True})
            self.assertEqual(item["summary"]["deltas"]["Banor.RecipeRun"], 1)
            self.assertEqual(item["conditions"], [])
        self.assert_witnesses(source, result)

    @unittest.skipUnless((OUTPUT / "Dlg_5C5D0C96.json").exists(), "Generated Miso graph is optional")
    def test_generated_miso_is_useful_despite_128_raw_route_cap(self):
        source = json.loads((OUTPUT / "Dlg_5C5D0C96.json").read_text(encoding="utf-8"))
        result = analyze_effect_outcomes(source)
        self.assertEqual(result["status"], "complete")
        self.assertGreater(len(result["outcomes"]), 1)
        self.assertLess(result["stats"]["discoveredStates"], 8192)
        self.assert_witnesses(source, result)


if __name__ == "__main__":
    unittest.main()
