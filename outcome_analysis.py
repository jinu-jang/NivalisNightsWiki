"""Bounded, static effect-combination analysis, independent of raw route enumeration.

``complete`` means all structural combinations, not verified game eligibility.
``partial`` indicates boundaries, dead ends, malformed transitions, or unsupported
effects; ``cycle`` additionally omits repeat traversals of potentially accumulating
cycles; ``limit`` indicates a resource budget was exhausted (highest precedence).

Literal sets and deltas compose in execution order. Summary assignments are final
literal values; summary deltas are relative to the initial value, never both for
one variable. Opaque scripts/actions are NOT simulated: their ordered payloads
and the literal transformations preceding each occurrence participate in state
identity. Summaries containing these are only the interpreted literal portion;
the witness's ``effects`` retains the actual order and repeated quantities.
"""

from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass
import hashlib
import heapq
import json
import math
import re


DEFAULT_MAX_STATES = 8192
_BOUNDARIES = {"external", "subdialogue"}
_TOKENS = re.compile(
    r'"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'|'
    r"[A-Za-z_]\w*|(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?|"
    r">>>=|===|!==|>>>|>>=|<<=|>=|<=|==|!=|&&|\|\||\+\+|--|"
    r"\+=|-=|\*=|/=|%=|&=|\|=|\^=|=>|<<|>>|\?\?|\?\.|[^\s]"
)


def _json(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def _condition_key(value):
    # Ignore formatting outside quoted literals, not whitespace inside strings.
    return tuple(_TOKENS.findall(value))


def _conditions(items):
    originals = {}
    for item in items:
        for value in item.get("conditions", []):
            if value.strip():
                originals.setdefault(_condition_key(value), value)
    return [originals[key] for key in sorted(originals)]


def _number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and (
        not isinstance(value, float) or math.isfinite(value)
    )


def _operation(effect):
    kind = effect.get("type")
    if kind in {"set", "delta"} and isinstance(effect.get("variable"), str):
        value = effect.get("value")
        if _number(value) or (kind == "set" and isinstance(value, bool)):
            return kind, effect["variable"], value
    if kind == "unlock_recipe" and isinstance(effect.get("recipe"), str):
        return "recipe", effect["recipe"], None
    return "opaque", _json(effect), None


@dataclass(frozen=True)
class _State:
    variables: tuple = ()
    recipes: tuple = ()
    opaque: tuple = ()
    conditions: frozenset = frozenset()

    def units(self):
        return 1 + len(self.variables) + len(self.recipes) + len(self.conditions) + sum(
            1 + len(variables) + len(recipes) for variables, recipes, _ in self.opaque
        )


def _literal_summary(state):
    deltas, assignments = {}, {}
    for variable, mode, value in state.variables:
        (assignments if mode == "set" else deltas)[variable] = json.loads(value)
    return deltas, assignments


def _summary(state):
    deltas, assignments = _literal_summary(state)
    actions, unparsed = [], []
    for _, _, payload in state.opaque:
        effect = json.loads(payload)
        if effect.get("type") == "template_action":
            actions.append(effect.get("label") or effect.get("action") or "Template action")
        else:
            unparsed.append(effect.get("script") or _json(effect))
    return {
        "deltas": deltas, "assignments": assignments, "recipes": list(state.recipes),
        "actions": actions, "unparsed": unparsed,
    }


def _components(reachable, adjacency):
    """Iterative Kosaraju: terminal/boundary outgoing edges are already excluded."""
    visited, order = set(), []
    for root in reachable:
        if root in visited:
            continue
        visited.add(root)
        stack = [(root, iter(adjacency[root]))]
        while stack:
            node, children = stack[-1]
            child = next(children, None)
            if child is None:
                order.append(node)
                stack.pop()
            elif child not in visited:
                visited.add(child)
                stack.append((child, iter(adjacency[child])))
    reverse = defaultdict(list)
    for source in reachable:
        for target in adjacency[source]:
            reverse[target].append(source)
    assigned, components = {}, []
    for root in reversed(order):
        if root in assigned:
            continue
        index = len(components)
        members, stack = [], [root]
        assigned[root] = index
        while stack:
            node = stack.pop()
            members.append(node)
            for source in reverse[node]:
                if source not in assigned:
                    assigned[source] = index
                    stack.append(source)
        components.append(members)
    return assigned, components, reverse


class _BudgetExceeded(Exception):
    pass


def analyze_effect_outcomes(graph, max_states=DEFAULT_MAX_STATES):
    """Return schema-version-2 ``graph.effectAnalysis`` without mutating ``graph``.

    States merge at each node on ordered literal transforms, recipe sets, ordered
    opaque effects, and normalized guard evidence, retaining a real route witness.
    Purely neutral/idempotent cycles converge. SCCs containing nonzero increments
    or opaque actions conservatively keep only loop-free traversals and advertise
    incomplete analysis, even when a particular game's loop might be bounded.

    Additional budgets bound transitions (32 times ``max_states``), effect
    operations/output effects (128 times), stored state units and output witness
    steps (64 times). A state unit is a retained variable, recipe, guard or opaque
    transform component. No recursion or saves are used.
    """
    if not isinstance(max_states, int) or isinstance(max_states, bool) or max_states < 1:
        raise ValueError("max_states must be a positive integer")
    nodes = {node["id"]: node for node in graph["nodes"]}
    if len(nodes) != len(graph["nodes"]):
        raise ValueError("Node IDs must be unique")
    outgoing, edge_by_id = defaultdict(list), {}
    for edge in graph["edges"]:
        outgoing[edge["from"]].append(edge)
        if edge.get("id") is not None:
            if edge["id"] in edge_by_id:
                raise ValueError("Edge IDs must be unique")
            edge_by_id[edge["id"]] = edge
    entry, exit_id = graph["entry"], graph["exit"]
    diagnostics, diagnostic_keys = [], set()
    incomplete = False

    def diagnose(kind, message, **details):
        diagnostic = {"kind": kind, "message": message, **details}
        key = _json(diagnostic)
        if key not in diagnostic_keys:
            diagnostic_keys.add(key)
            diagnostics.append(diagnostic)

    def terminal(identifier):
        return identifier == exit_id

    reachable, adjacency = [], defaultdict(list)
    pending, reached = deque([entry]), {entry}
    while pending:
        identifier = pending.popleft()
        if identifier not in nodes:
            continue
        reachable.append(identifier)
        if terminal(identifier) or nodes[identifier].get("type") in _BOUNDARIES:
            continue
        for edge in outgoing[identifier]:
            if edge.get("id") is None or edge["to"] not in nodes:
                continue
            adjacency[identifier].append(edge["to"])
            if edge["to"] not in reached:
                reached.add(edge["to"])
                pending.append(edge["to"])
    component_by_node, components, reverse = _components(reachable, adjacency)
    exit_distance = {exit_id: 0} if exit_id in reached and exit_id in nodes else {}
    pending = deque(exit_distance)
    while pending:
        identifier = pending.popleft()
        for source in reverse[identifier]:
            if source not in exit_distance:
                exit_distance[source] = exit_distance[identifier] + 1
                pending.append(source)
    can_exit = set(exit_distance)

    boolean_variables = {
        effect["variable"]
        for identifier in reachable
        for item in [nodes[identifier]] + (
            [] if terminal(identifier) or nodes[identifier].get("type") in _BOUNDARIES
            else outgoing[identifier]
        )
        for effect in item.get("effects", [])
        if effect.get("type") == "set" and isinstance(effect.get("value"), bool)
        and isinstance(effect.get("variable"), str)
    }
    dangerous, cycle_count = set(), 0
    for index, members in enumerate(components):
        if len(members) == 1 and members[0] not in adjacency[members[0]]:
            continue
        cycle_count += 1
        member_set = set(members)
        internal_edges = [
            edge for member in members for edge in outgoing[member]
            if edge.get("id") is not None and edge["to"] in member_set
        ]
        items = [nodes[member] for member in members] + internal_edges
        accumulating = any(
            operation[0] == "opaque" or (
                operation[0] == "delta"
                and (operation[2] != 0 or operation[1] in boolean_variables)
            )
            for item in items for operation in map(_operation, item.get("effects", []))
        )
        if accumulating:
            dangerous.add(index)
        diagnose(
            "effectful_cycle" if accumulating else "convergent_cycle",
            "Repeat traversals omitted; loop-free outcomes are not maxima."
            if accumulating else "Neutral or idempotent effects merge to a fixed point.",
            nodes=sorted(members), edges=sorted(edge["id"] for edge in internal_edges),
            conditions=_conditions(items), canReachExit=bool(member_set & can_exit),
            effects=[
                {"node": member, "effects": nodes[member]["effects"]}
                for member in members if nodes[member].get("effects")
            ] + [
                {"edge": edge["id"], "effects": edge["effects"]}
                for edge in internal_edges if edge.get("effects")
            ],
        )

    stats = {
        "totalNodes": len(nodes), "totalEdges": len(graph["edges"]),
        "reachableNodes": len(reachable),
        "effectBearingNodes": sum(bool(node.get("effects")) for node in nodes.values()),
        "effectBearingEdges": sum(bool(edge.get("effects")) for edge in graph["edges"]),
        "processedStates": 0, "discoveredStates": 0, "mergedStates": 0,
        "processedTransitions": 0, "effectOperations": 0,
        "stateLimit": max_states, "transitionLimit": max_states * 32,
        "effectOperationLimit": max_states * 128,
        "stateUnits": 0, "stateUnitLimit": max_states * 64,
        "witnessSteps": 0, "witnessStepLimit": max_states * 64,
        "outputEffects": 0, "outputEffectLimit": max_states * 128,
        "cycleComponents": cycle_count, "omittedCycleTransitions": 0,
        "boundaries": 0, "deadEnds": 0, "outcomes": 0,
    }
    operations = {}

    def advance(state, item, location):
        nonlocal incomplete
        guards = state.conditions | frozenset(
            _condition_key(value) for value in item.get("conditions", []) if value.strip()
        )
        variables = {variable: (mode, value) for variable, mode, value in state.variables}
        recipes, opaque = set(state.recipes), state.opaque
        opaque_units = sum(1 + len(before) + len(unlocks) for before, unlocks, _ in opaque)
        if location not in operations:
            operations[location] = [
                (effect, _operation(effect)) for effect in item.get("effects", [])
            ]
        for effect, (kind, variable, value) in operations[location]:
            if stats["effectOperations"] >= stats["effectOperationLimit"]:
                raise _BudgetExceeded("Effect-operation budget exhausted")
            stats["effectOperations"] += 1
            if kind == "set":
                variables[variable] = ("set", _json(value))
            elif kind == "delta":
                mode, previous = variables.get(variable, ("delta", "0"))
                previous = json.loads(previous)
                if isinstance(previous, bool):
                    kind, variable = "opaque", _json(effect)
                else:
                    total = previous + value
                    if not _number(total):
                        kind, variable = "opaque", _json(effect)
                    elif mode == "delta" and total == 0:
                        variables.pop(variable, None)
                    else:
                        variables[variable] = (mode, _json(total))
            elif kind == "recipe":
                recipes.add(variable)
            if kind == "opaque":
                incomplete = True
                opaque_units += 1 + len(variables) + len(recipes)
                if opaque_units > stats["stateUnitLimit"]:
                    raise _BudgetExceeded("Stored effect-state size budget exhausted")
                before = tuple(sorted((name, *transform) for name, transform in variables.items()))
                opaque += ((before, tuple(sorted(recipes)), variable),)
                diagnose(
                    "unsupported_effect",
                    "Script/action intent retained in order, not simulated; literal summary may be incomplete.",
                    **{location[0]: location[1]}, effect=effect,
                )
            if 1 + len(variables) + len(recipes) + len(guards) + opaque_units > stats["stateUnitLimit"]:
                raise _BudgetExceeded("Stored effect-state size budget exhausted")
        return _State(
            tuple(sorted((name, *transform) for name, transform in variables.items())),
            tuple(sorted(recipes)), opaque, guards,
        )

    # Predecessor records avoid copying long witness paths into every queued state.
    records, seen, queue, completed = [], set(), [], []

    def enqueue(identifier, state, parent, edge_id, visited):
        if identifier not in nodes:
            return
        state = advance(state, nodes[identifier], ("node", identifier))
        key = (identifier, state, visited)
        if key in seen:
            stats["mergedStates"] += 1
            return
        if len(records) >= max_states:
            raise _BudgetExceeded("Unique effect-state budget exhausted")
        units = state.units() + len(visited)
        if stats["stateUnits"] + units > stats["stateUnitLimit"]:
            raise _BudgetExceeded("Stored effect-state size budget exhausted")
        stats["stateUnits"] += units
        seen.add(key)
        record = len(records)
        records.append((identifier, state, parent, edge_id, visited))
        # Prefer exits so a capped analysis still has useful, genuine witnesses.
        heapq.heappush(queue, (exit_distance.get(identifier, len(nodes) + 1), record))
        if terminal(identifier):
            completed.append(record)

    exhausted = False
    try:
        if entry not in nodes:
            incomplete = True
            diagnose("missing_entry", "Conversation entry is absent.", node=entry)
        else:
            visited = frozenset({entry}) if component_by_node.get(entry) in dangerous else frozenset()
            enqueue(entry, _State(), None, None, visited)
        while queue:
            _, record = heapq.heappop(queue)
            identifier, state, _, _, visited = records[record]
            stats["processedStates"] += 1
            if terminal(identifier):
                continue
            node = nodes[identifier]
            if node.get("type") in _BOUNDARIES:
                incomplete = True
                stats["boundaries"] += 1
                diagnose("boundary", "External/subdialogue behavior is outside this analysis.",
                         node=identifier, nodeType=node["type"])
                continue
            if not outgoing[identifier]:
                incomplete = True
                stats["deadEnds"] += 1
                diagnose("dead_end", "Reached a dead end before the conversation exit.", node=identifier)
            for edge in outgoing[identifier]:
                if stats["processedTransitions"] >= stats["transitionLimit"]:
                    raise _BudgetExceeded("Transition budget exhausted")
                stats["processedTransitions"] += 1
                target = edge["to"]
                if edge.get("id") is None or target not in nodes:
                    incomplete = True
                    diagnose("invalid_transition", "Missing real edge ID or target; transition omitted.",
                             node=identifier, edge=edge.get("id"), target=target)
                    continue
                target_component = component_by_node.get(target)
                if target_component in dangerous:
                    previous = visited if component_by_node.get(identifier) == target_component else frozenset()
                    if target in previous:
                        stats["omittedCycleTransitions"] += 1
                        continue
                    next_visited = previous | {target}
                else:
                    next_visited = frozenset()
                next_state = advance(state, edge, ("edge", edge["id"]))
                enqueue(target, next_state, record, edge["id"], next_visited)
    except _BudgetExceeded as error:
        exhausted = True
        diagnose("resource_limit", str(error))
    stats["discoveredStates"] = len(records)
    if exit_id not in can_exit or entry not in can_exit:
        incomplete = True
        diagnose("unreachable_exit", "No structural entry-to-exit route within conversation boundaries.")

    outcomes = []
    for record in completed:
        _, state, _, _, _ = records[record]
        route_nodes, route_edges = [], []
        cursor = record
        while cursor is not None:
            if stats["witnessSteps"] >= stats["witnessStepLimit"]:
                exhausted = True
                diagnose("resource_limit", "Witness-output budget exhausted")
                break
            stats["witnessSteps"] += 1
            identifier, _, parent, edge_id, _ = records[cursor]
            route_nodes.append(identifier)
            if edge_id is not None:
                route_edges.append(edge_id)
            cursor = parent
        if cursor is not None:
            break
        route_nodes.reverse()
        route_edges.reverse()
        ordered_items = []
        for index, identifier in enumerate(route_nodes):
            ordered_items.append(nodes[identifier])
            if index < len(route_edges):
                ordered_items.append(edge_by_id[route_edges[index]])
        ordered_effects = [effect for item in ordered_items for effect in item.get("effects", [])]
        if stats["outputEffects"] + len(ordered_effects) > stats["outputEffectLimit"]:
            exhausted = True
            diagnose("resource_limit", "Effect-output budget exhausted")
            break
        stats["outputEffects"] += len(ordered_effects)
        signature = (
            state.variables, state.recipes, state.opaque, sorted(state.conditions),
        )
        outcomes.append({
            "id": "outcome-" + hashlib.sha256(_json(signature).encode("utf-8")).hexdigest()[:20],
            "summary": _summary(state), "conditions": _conditions(ordered_items),
            "effects": ordered_effects,
            "witness": {
                "nodes": route_nodes, "edges": route_edges,
                "choices": [identifier for identifier in route_nodes if nodes[identifier].get("type") == "choice"],
            },
        })
    outcomes.sort(key=lambda outcome: outcome["id"])
    stats["outcomes"] = len(outcomes)
    status = "limit" if exhausted else "cycle" if dangerous else "partial" if incomplete else "complete"
    descriptions = {
        "complete": "All structural effect combinations; guards are retained, not evaluated.",
        "partial": "Known exit outcomes only; boundaries, dead ends, or unsupported behavior remain.",
        "cycle": "Known loop-free exit outcomes only; effectful cycles may repeat, so no maxima are claimed.",
        "limit": "Known exit outcomes only; an effect-state resource budget was exhausted.",
    }
    return {
        "schemaVersion": 2, "status": status, "complete": status == "complete",
        "method": "merged-effect-states-v1", "message": descriptions[status],
        "outcomes": outcomes, "stats": stats, "diagnostics": diagnostics,
    }
