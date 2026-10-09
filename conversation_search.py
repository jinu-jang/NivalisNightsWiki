"""Compact, lossless spoken-content index for the static dialogue guide."""

from __future__ import annotations


_SPOKEN_TYPES = {"line", "choice"}
_NODE_TYPES = _SPOKEN_TYPES | {
    "action", "condition", "entry", "exit", "external", "subdialogue",
}
_PRONOUN_FIELDS = ("TextHeVersion", "TextSheVersion", "TextTheyVersion")


def _string(value, label, *, identifier=False):
    if not isinstance(value, str):
        raise ValueError(f"{label} must be a string")
    if any(0xD800 <= ord(character) <= 0xDFFF for character in value):
        raise ValueError(f"{label} contains an invalid Unicode surrogate")
    if identifier and (
        not value.strip()
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
    ):
        raise ValueError(f"{label} must be a nonempty identifier without control characters")
    return value


def _meaningful(text):
    return bool(text.strip()) and text.strip() != "~"


class SearchIndexBuilder:
    """Collect graph projections without retaining source assets or graph payloads.

    Version 2 documents contain ``id``, ``title``, ``speakers``, and ``lines``.
    Each line is the compact tuple [node ID, "line"|"choice", speaker|null, text].
    Distinct pronoun wording uses additional tuples with the same node ID.
    """

    def __init__(self):
        self._documents = {}

    def add(self, graph):
        if not isinstance(graph, dict):
            raise ValueError("Dialogue graph must be an object")
        if "schemaVersion" in graph and (
            type(graph["schemaVersion"]) is not int or graph["schemaVersion"] != 2
        ):
            raise ValueError("Dialogue graph must use schema version 2")
        identifier = _string(graph.get("dialogueId"), "Dialogue ID", identifier=True)
        title = _string(graph.get("title"), "Dialogue title")
        nodes = graph.get("nodes")
        if not isinstance(nodes, list):
            raise ValueError(f"Dialogue {identifier}: nodes must be an array")
        speakers = graph.get("speakers", [])
        if not isinstance(speakers, list):
            raise ValueError(f"Dialogue {identifier}: speakers must be an array")
        speakers = {_string(speaker, "Dialogue speaker") for speaker in speakers}
        speakers = {speaker for speaker in speakers if speaker.strip()}
        seen_nodes = {}
        lines = set()
        for node in nodes:
            if not isinstance(node, dict):
                raise ValueError(f"Dialogue {identifier}: every node must be an object")
            node_id = _string(node.get("id"), "Node ID", identifier=True)
            kind = node.get("type")
            if not isinstance(kind, str) or kind not in _NODE_TYPES:
                raise ValueError(f"Dialogue {identifier}: invalid node type for {node_id}")
            text = _string(node.get("text"), f"Node {node_id} text")
            speaker = node.get("speaker")
            if speaker is not None:
                speaker = _string(speaker, f"Node {node_id} speaker")
            pronouns = node.get("pronounText")
            if pronouns is not None and not isinstance(pronouns, dict):
                raise ValueError(f"Node {node_id}: pronounText must be an object or null")
            variants = {text} if _meaningful(text) else set()
            if pronouns is not None:
                for field in _PRONOUN_FIELDS:
                    variant = pronouns.get(field)
                    if variant is not None:
                        variant = _string(variant, f"Node {node_id} {field}")
                        if _meaningful(variant):
                            variants.add(variant)
            projection = (kind, speaker, text, tuple(sorted(variants)))
            if node_id in seen_nodes and seen_nodes[node_id] != projection:
                raise ValueError(f"Dialogue {identifier}: conflicting duplicate node ID {node_id}")
            seen_nodes[node_id] = projection
            if kind in _SPOKEN_TYPES:
                if speaker and speaker.strip():
                    speakers.add(speaker)
                lines.update((node_id, kind, speaker, variant) for variant in variants)
        document = {
            "id": identifier, "title": title, "speakers": sorted(speakers),
            "lines": [
                list(line) for line in sorted(
                    lines, key=lambda line: (line[0], line[1], line[2] or "", line[3])
                )
            ],
        }
        previous = self._documents.get(identifier)
        if previous is not None and previous != document:
            raise ValueError(f"Conflicting duplicate dialogue ID {identifier}")
        self._documents[identifier] = document

    def build(self):
        """Return a deterministic JSON-ready snapshot independent of insertion order."""
        return {
            "schemaVersion": 2,
            "dialogues": [
                {
                    "id": document["id"], "title": document["title"],
                    "speakers": list(document["speakers"]),
                    "lines": [list(line) for line in document["lines"]],
                }
                for _, document in sorted(self._documents.items())
            ],
        }
