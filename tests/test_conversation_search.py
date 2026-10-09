import copy
import json
import unittest

from conversation_search import SearchIndexBuilder


# Evaluate this expression in the existing Edge verifier on a page beside search.js.
FRONTEND_EXPRESSION = r"""
(async () => {
  const { ConversationSearch, ConversationSearchError } = await import(new URL("search.js", location.href));
  const speech = "🙂 I’m listening.\nCafé <img src=x onerror=alert(1)>.";
  const fixture = {
    schemaVersion: 2, dialogues: [
      { id: "d1", title: "An authored title", speakers: ["Nola Berg"], lines: [
        ["reply", "choice", "Player", speech],
        ["tea", "line", "Nola Berg", "She delivered the tea."],
        ["noodles", "line", "Nola Berg", "The noodles are ready."]
      ] },
      { id: "d2", title: "I'm listening", speakers: ["Judy"], lines: [] },
      { id: "d3", title: "Other", speakers: ["Nola Berg"], lines: [
        ["variant", "line", "Nola Berg", "He gave his word."],
        ["variant", "line", "Nola Berg", "She gave her word."],
        ["variant", "line", "Nola Berg", "They gave their word."]
      ] },
      { id: "d4", title: "Across nodes", speakers: ["Tala"], lines: [
        ["first", "line", "Tala", "Bring noodles."],
        ["second", "line", "Tala", "Tea is hot."]
      ] },
      { id: "d5", title: "Unicode", speakers: [], lines: [
        ["unicode", "line", null, "Cafe\u0301 — Æsir, Straße, ﬁancé 🙂"]
      ] },
      { id: "d6", title: "First", speakers: [], lines: [
        ["n2", "line", null, "Same wording."],
        ["n1", "line", null, "Same wording."]
      ] },
      { id: "d7", title: "Second", speakers: [], lines: [
        ["n1", "line", null, "Same wording."]
      ] },
      { id: "__proto__", title: "Map keys", speakers: [], lines: [
        ["__proto__", "line", null, "Prototype-safe speech."]
      ] }
    ]
  };
  let calls = 0;
  const engine = new ConversationSearch("search-fixture.json", { loadJSON: async () => { calls++; return fixture; } });
  const assert = (condition, message) => { if (!condition) throw new Error(message); };
  const equal = (actual, expected, message) => assert(JSON.stringify(actual) === JSON.stringify(expected), message);
  const rejected = async (promise, predicate, message) => {
    try { await promise; } catch (error) { assert(predicate(error), message + ": " + error); return; }
    throw new Error(message + ": did not reject");
  };
  const hit = async (query, id = "d1") => (await engine.search(query)).find(item => item.dialogueId === id);
  const cases = [
    ["empty queries do not load the index", async () => {
      equal(await engine.search("  "), [], "empty result");
      equal(await engine.search('""'), [], "empty quotes");
      equal(calls, 0, "index loaded eagerly");
    }],
    ["straight and curly apostrophes match original speech", async () => {
      for (const query of ["I'm listening", "I’m listening", "Iʼm listening"]) {
        const result = await hit(query);
        assert(result?.nodeId === "reply" && result.kind === "choice", query);
        equal(result.text, speech, "text was normalized or rewritten");
      }
    }],
    ["quoted phrases and smart quotes are contiguous", async () => {
      for (const query of ['"I\'m listening"', "“I’m listening”"]) {
        const result = await hit(query);
        equal(result.matchRanges, [[3, 16]], "quoted phrase range");
      }
      equal(await engine.search('"noodles tea"'), [], "phrase crossed a node boundary");
    }],
    ["content ranks above authored metadata", async () => {
      const results = await engine.search("I'm listening");
      equal(results.map(item => item.dialogueId), ["d1", "d2"], "ranking");
      assert(results[0].score > results[1].score, "content has no ranking advantage");
    }],
    ["substring search is case insensitive", async () => {
      assert((await hit("LiStEn"))?.nodeId === "reply", "substring match");
    }],
    ["all query terms are required", async () => {
      equal(await engine.search("listening nonexistent-token"), [], "missing term accepted");
    }],
    ["actor metadata combines with content terms", async () => {
      for (const query of ["Nola Berg listening", '"Nola Berg" listening']) {
        const result = await hit(query);
        assert(result?.nodeId === "reply" && result.matchesElsewhere, "actor plus speech");
        equal(result.matchRanges, [[7, 16]], "metadata highlighted in speech");
      }
    }],
    ["cross-node matching explicitly reports partial snippets", async () => {
      const result = await hit("noodles tea", "d4");
      assert(result?.matchesElsewhere && result.matchedTokenCount === 1 && result.totalTokenCount === 2, "cross-node coverage");
      for (const [start, end] of result.matchRanges) assert(/noodles|tea/i.test(result.text.slice(start, end)), "wrong highlighted term");
    }],
    ["metadata-only matches never focus a node", async () => {
      const results = await engine.search("Nola Berg");
      assert(results.length === 2, "metadata speakers");
      assert(results.every(item => item.nodeId === null && item.kind === "metadata"), "metadata focused a spoken node");
      const result = await hit("authored title");
      assert(result.nodeId === null && result.text === "An authored title" && !result.matchesElsewhere, "metadata text");
    }],
    ["diacritics and composed accents have exact original offsets", async () => {
      const result = await hit("cafe");
      equal(result.matchRanges.map(([start, end]) => result.text.slice(start, end)), ["Café"], "composed accent");
      const decomposed = await hit("café", "d5");
      equal(decomposed.matchRanges, [[0, 5]], "combining accent excluded from highlight");
    }],
    ["ligatures and folded Unicode match safely", async () => {
      for (const [query, original] of [["aesir", "Æsir"], ["strasse", "Straße"], ["fiance", "ﬁancé"]]) {
        const result = await hit(query, "d5");
        equal(result.matchRanges.map(([start, end]) => result.text.slice(start, end)), [original], query);
      }
    }],
    ["UTF-16 offsets do not split emoji", async () => {
      const result = await hit("🙂");
      equal(result.matchRanges, [[0, 2]], "surrogate pair offsets");
    }],
    ["markup remains inert exact text", async () => {
      const result = await hit("onerror");
      equal(result.text, speech, "markup was changed");
      equal(result.matchRanges.map(([start, end]) => result.text.slice(start, end)), ["onerror"], "unsafe markup offset");
    }],
    ["pronoun variants return their actual wording and shared node", async () => {
      const result = await hit("her word", "d3");
      equal(result.text, "She gave her word.", "variant wording");
      equal(result.nodeId, "variant", "variant node");
    }],
    ["same wording retains dialogue targets but returns one representative", async () => {
      const results = await engine.search("Same wording");
      equal(results.map(item => [item.dialogueId, item.nodeId]), [["d6", "n1"], ["d7", "n1"]], "deduplication targets");
    }],
    ["prototype-looking identifiers are safe", async () => {
      equal((await hit("prototype-safe", "__proto__")).nodeId, "__proto__", "prototype key");
    }],
    ["successful index downloads are cached", async () => {
      await Promise.all([engine.search("tea"), engine.search("listening")]);
      equal(calls, 1, "index reloaded");
    }],
    ["pre-aborted caller does not start a download", async () => {
      let loads = 0;
      const instance = new ConversationSearch("index.json", { loadJSON: async () => { loads++; return fixture; } });
      const controller = new AbortController(); controller.abort();
      await rejected(instance.search("tea", { signal: controller.signal }), error => error.name === "AbortError", "pre-abort");
      equal(loads, 0, "download started for aborted query");
    }],
    ["aborting one concurrent caller does not poison another or retry", async () => {
      let complete, loads = 0;
      const instance = new ConversationSearch("index.json", { loadJSON: () => { loads++; return new Promise(resolve => { complete = resolve; }); } });
      const controller = new AbortController();
      const cancelled = instance.search("listening", { signal: controller.signal });
      const cancelledCheck = rejected(cancelled, error => error.name === "AbortError", "shared abort");
      const survivor = instance.search("tea");
      await Promise.resolve(); controller.abort(); await cancelledCheck;
      complete(fixture);
      assert((await survivor).length > 0, "surviving caller");
      assert((await instance.search("listening")).length > 0, "retry after cancellation");
      equal(loads, 1, "caller abortion poisoned shared download");
    }],
    ["aborting a cached query is observed", async () => {
      const controller = new AbortController();
      const pending = engine.search("listening", { signal: controller.signal });
      controller.abort();
      await rejected(pending, error => error.name === "AbortError", "cached query abort");
      assert((await engine.search("listening")).length > 0, "cache poisoned");
    }],
    ["failed downloads after an aborted caller can be retried", async () => {
      let failDownload, loads = 0;
      const instance = new ConversationSearch("index.json", { loadJSON: () => {
        if (++loads === 1) return new Promise((resolve, reject) => { failDownload = reject; });
        return fixture;
      } });
      const controller = new AbortController();
      const pending = rejected(instance.search("tea", { signal: controller.signal }), error => error.name === "AbortError", "abort before failure");
      await Promise.resolve(); controller.abort(); await pending;
      failDownload(new Error("offline"));
      await new Promise(resolve => setTimeout(resolve, 0));
      assert((await instance.search("tea")).length > 0, "aborted failed download prevented retry");
      equal(loads, 2, "failed shared download retained");
    }],
    ["loader errors are explicit and successful retries are cached", async () => {
      let loads = 0;
      const failure = new Error("GuideDataError"); failure.kind = "network";
      const instance = new ConversationSearch("index.json", { loadJSON: async () => { if (++loads === 1) throw failure; return fixture; } });
      await rejected(instance.search("tea"), error => error === failure, "loader error not propagated");
      assert((await instance.search("tea")).length > 0, "retry failed");
      await instance.search("listening");
      equal(loads, 2, "failed or successful cache behavior");
    }],
    ["schema version and malformed document errors are explicit", async () => {
      const invalid = [
        null, [], { schemaVersion: 1, dialogues: [] }, { schemaVersion: 2, dialogues: {} },
        { schemaVersion: 2, dialogues: [{ id: 123, title: "x", speakers: [], lines: [] }] },
        { schemaVersion: 2, dialogues: [{ id: " ", title: "x", speakers: [], lines: [] }] },
        { schemaVersion: 2, dialogues: [{ id: "a", title: null, speakers: [], lines: [] }] },
        { schemaVersion: 2, dialogues: [{ id: "a", title: "x", speakers: "Nola", lines: [] }] },
        { schemaVersion: 2, dialogues: [{ id: "a", title: "x", speakers: [], lines: {} }] }
      ];
      for (const value of invalid) {
        await rejected(new ConversationSearch("index.json", { loadJSON: async () => value }).search("x"), error => error instanceof ConversationSearchError && error.kind === "schema", "invalid document");
      }
    }],
    ["invalid spoken rows and conflicting IDs are rejected", async () => {
      const invalidRows = [
        ["node", "line", null], [42, "line", null, "x"], ["node", "action", null, "x"],
        ["node", "line", {}, "x"], ["node", "line", null, ""], ["node", "line", null, " ~ "],
        ["node", "line", null, "bad\ud800"]
      ];
      const invalid = invalidRows.map(row => ({ schemaVersion: 2, dialogues: [{ id: "a", title: "x", speakers: [], lines: [row] }] }));
      invalid.push({ schemaVersion: 2, dialogues: [fixture.dialogues[0], fixture.dialogues[0]] });
      invalid.push({ schemaVersion: 2, dialogues: [{ id: "a", title: "x", speakers: [], lines: [
        ["node", "line", "Nola", "x"], ["node", "choice", "Player", "y"]
      ] }] });
      for (const value of invalid) {
        await rejected(new ConversationSearch("index.json", { loadJSON: async () => value }).search("x"), error => error.kind === "schema", "invalid rows");
      }
    }],
    ["a malformed index does not prevent a later valid retry", async () => {
      let loads = 0;
      const instance = new ConversationSearch("index.json", { loadJSON: async () => ++loads === 1 ? {} : fixture });
      await rejected(instance.search("tea"), error => error.kind === "schema", "malformed index");
      assert((await instance.search("tea")).length > 0, "valid retry");
      equal(loads, 2, "malformed cache was retained");
    }],
    ["the default fetch loader surfaces HTTP JSON and connection errors", async () => {
      const originalFetch = globalThis.fetch;
      const cases = [
        [async () => new Response("missing", { status: 404 }), "network"],
        [async () => new Response("{"), "json"],
        [async () => { throw new TypeError("offline"); }, "network"]
      ];
      try {
        for (const [stub, kind] of cases) {
          globalThis.fetch = (url, ...args) => String(url) === "search-test-errors.json" ? stub() : originalFetch(url, ...args);
          await rejected(new ConversationSearch("search-test-errors.json").search("tea"), error => error.kind === kind, "default loader " + kind);
        }
      } finally { globalThis.fetch = originalFetch; }
    }],
    ["constructor and query shape errors are explicit", async () => {
      for (const value of [null, 42, "", " "]) {
        let failure; try { new ConversationSearch(value); } catch (error) { failure = error; }
        assert(failure instanceof TypeError, "invalid constructor URL");
      }
      await rejected(engine.search(null), error => error instanceof TypeError, "invalid query");
    }]
  ];
  const failures = [];
  for (const [name, test] of cases) {
    try { await test(); } catch (error) { failures.push({ name, error: String(error.stack || error) }); }
  }
  return { passed: cases.length - failures.length, failed: failures.length, failures };
})()
"""


def node(identifier="n1", text="Hello.", kind="line", speaker="Nola Berg", **extra):
    return {"id": identifier, "type": kind, "text": text, "speaker": speaker, **extra}


def graph(identifier="d1", title="An authored title", nodes=None, **extra):
    return {
        "schemaVersion": 2, "dialogueId": identifier, "title": title,
        "nodes": [node()] if nodes is None else nodes, **extra,
    }


class ConversationSearchTests(unittest.TestCase):
    def index(self, *graphs):
        builder = SearchIndexBuilder()
        for document in graphs:
            builder.add(document)
        return builder.build()

    def test_empty_index_is_versioned(self):
        self.assertEqual(self.index(), {"schemaVersion": 2, "dialogues": []})

    def test_npc_speech_and_player_replies_are_searchable(self):
        document = self.index(graph(nodes=[
            node("npc", "Stay for a bowl."),
            node("reply", "I'm listening.", "choice", "Player"),
        ]))["dialogues"][0]
        self.assertEqual(document["lines"], [
            ["npc", "line", "Nola Berg", "Stay for a bowl."],
            ["reply", "choice", "Player", "I'm listening."],
        ])
        self.assertEqual(document["speakers"], ["Nola Berg", "Player"])

    def test_distinct_meaningful_pronoun_versions_keep_their_node(self):
        document = self.index(graph(nodes=[node(text="He is here.", pronounText={
            "TextHeVersion": "He is here.", "TextSheVersion": "She is here.",
            "TextTheyVersion": "They are here.",
        })]))["dialogues"][0]
        self.assertEqual([line[3] for line in document["lines"]], [
            "He is here.", "She is here.", "They are here.",
        ])
        self.assertTrue(all(line[0] == "n1" for line in document["lines"]))

    def test_player_pronoun_versions_are_also_included(self):
        document = self.index(graph(nodes=[node(
            text="I saw him.", kind="choice", speaker="Player",
            pronounText={"TextHeVersion": "I saw him.", "TextSheVersion": "I saw her."},
        )]))["dialogues"][0]
        self.assertEqual([line[3] for line in document["lines"]], ["I saw her.", "I saw him."])

    def test_placeholder_and_missing_variants_are_excluded(self):
        document = self.index(graph(nodes=[
            node("empty", "", pronounText=None),
            node("placeholder", " ~ ", pronounText={
                "TextHeVersion": "~", "TextSheVersion": None, "TextTheyVersion": "\t",
            }),
            node("variant", "~", pronounText={"TextSheVersion": "Real speech."}),
        ]))["dialogues"][0]
        self.assertEqual(document["lines"], [["variant", "line", "Nola Berg", "Real speech."]])
        self.assertEqual(document["speakers"], ["Nola Berg"])

    def test_identical_variants_and_repeated_node_references_are_deduplicated(self):
        repeated = node(pronounText=dict.fromkeys(
            ["TextHeVersion", "TextSheVersion", "TextTheyVersion"], "Hello."
        ))
        document = self.index(graph(nodes=[repeated, copy.deepcopy(repeated)]))["dialogues"][0]
        self.assertEqual(document["lines"], [["n1", "line", "Nola Berg", "Hello."]])

    def test_same_wording_at_different_nodes_and_dialogues_keeps_all_targets(self):
        documents = self.index(
            graph("d1", nodes=[node("n1"), node("n2")]),
            graph("d2", nodes=[node("n1")]),
        )["dialogues"]
        self.assertEqual([line[0] for line in documents[0]["lines"]], ["n1", "n2"])
        self.assertEqual(documents[1]["lines"][0][0], "n1")
        self.assertEqual([document["id"] for document in documents], ["d1", "d2"])

    def test_original_unicode_whitespace_and_markup_are_retained_as_plain_text(self):
        text = "  I’m listening — café.\n<em>🙂</em>  "
        document = self.index(graph(nodes=[node(text=text)]))["dialogues"][0]
        self.assertEqual(document["lines"][0][3], text)
        self.assertEqual(json.loads(json.dumps(document, ensure_ascii=False)), document)

    def test_nonspoken_nodes_do_not_leak_instructions_or_external_titles(self):
        nodes = [
            node("spoken", "Welcome."),
            *[node(kind, "secret " + kind, kind, None) for kind in
              ["entry", "exit", "action", "condition", "external", "subdialogue"]],
        ]
        document = self.index(graph(nodes=nodes))["dialogues"][0]
        self.assertEqual(document["lines"], [["spoken", "line", "Nola Berg", "Welcome."]])
        self.assertNotIn("secret", json.dumps(document))

    def test_metadata_only_dialogues_are_retained(self):
        document = self.index(graph(nodes=[], speakers=["Nola Berg"]))["dialogues"][0]
        self.assertEqual(document, {
            "id": "d1", "title": "An authored title", "speakers": ["Nola Berg"], "lines": [],
        })

    def test_source_assets_absolute_paths_and_unknown_pronoun_fields_are_not_copied(self):
        document = self.index(graph(
            provenance={"gameDirectory": r"C:\private\game"},
            raw={"credential": "not-index-content"},
            nodes=[node(raw={"mText": "raw asset"}, pronounText={"raw": "not-index-content"})],
        ))
        encoded = json.dumps(document)
        for excluded in ["gameDirectory", "credential", "not-index-content", "raw asset", "private"]:
            self.assertNotIn(excluded, encoded)

    def test_order_is_stable_across_graph_node_speaker_and_variant_order(self):
        first = graph("z", nodes=[
            node("z"), node("a", "Other", pronounText={
                "TextSheVersion": "She", "TextHeVersion": "He",
            }),
        ], speakers=["Z", "A", "Z"])
        second = graph("a")
        shuffled = copy.deepcopy(first)
        shuffled["nodes"].reverse()
        shuffled["speakers"].reverse()
        self.assertEqual(
            json.dumps(self.index(first, second), ensure_ascii=False),
            json.dumps(self.index(second, shuffled), ensure_ascii=False),
        )

    def test_readding_identical_dialogue_is_idempotent(self):
        document = graph()
        self.assertEqual(self.index(document, copy.deepcopy(document)), self.index(document))

    def test_conflicting_dialogue_ids_are_rejected_without_overwriting(self):
        builder = SearchIndexBuilder()
        builder.add(graph())
        before = builder.build()
        for conflicting in [graph(title="Different"), graph(nodes=[node(text="Different")])]:
            with self.subTest(conflicting=conflicting), self.assertRaisesRegex(
                ValueError, "duplicate dialogue ID"
            ):
                builder.add(conflicting)
            self.assertEqual(builder.build(), before)

    def test_conflicting_node_ids_are_rejected(self):
        original = node()
        for conflicting in [
            node(text="Different"), node(kind="choice"), node(speaker="Someone else"),
            node(pronounText={"TextSheVersion": "Different"}),
            node(kind="action"),
        ]:
            with self.subTest(conflicting=conflicting), self.assertRaisesRegex(
                ValueError, "duplicate node ID"
            ):
                self.index(graph(nodes=[original, conflicting]))

    def test_invalid_graph_shapes_are_explicit(self):
        bad = [
            None, [], "", graph(schemaVersion=1), graph(schemaVersion=True),
            graph(title=None), graph(nodes={}), graph(nodes="nodes"),
            graph(speakers="Nola"), graph(speakers=[None]), graph(nodes=[None]),
        ]
        for document in bad:
            with self.subTest(document=document), self.assertRaises(ValueError):
                self.index(document)

    def test_invalid_node_shapes_are_explicit(self):
        bad = [
            node(kind=None), node(kind=[]), node(kind="script"), node(text=None),
            node(text={"html": "not text"}), node(speaker=42), node(speaker=[]),
            node(pronounText=[]), node(pronounText="text"),
            node(pronounText={"TextSheVersion": []}),
            node(pronounText={"TextTheyVersion": False}),
        ]
        for invalid in bad:
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                self.index(graph(nodes=[invalid]))

    def test_ids_must_be_lossless_nonempty_strings_without_controls(self):
        bad_ids = [None, 72057714297016594, True, [], {}, "", " ", "a\0b", "a\nb", "a\x7fb"]
        for identifier in bad_ids:
            with self.subTest(dialogue=identifier), self.assertRaises(ValueError):
                self.index(graph(identifier))
            with self.subTest(node=identifier), self.assertRaises(ValueError):
                self.index(graph(nodes=[node(identifier)]))
        identifier = "72057714297016594"
        self.assertEqual(self.index(graph(identifier))["dialogues"][0]["id"], identifier)

    def test_malformed_unicode_is_rejected_before_export(self):
        for document in [
            graph("bad\ud800"), graph(title="bad\udfff"), graph(nodes=[node(text="bad\ud800")]),
            graph(nodes=[node(pronounText={"TextSheVersion": "bad\udfff"})]),
            graph(speakers=["bad\udfff"]),
        ]:
            with self.subTest(document=document), self.assertRaisesRegex(ValueError, "surrogate"):
                self.index(document)

    def test_build_returns_an_independent_snapshot(self):
        builder = SearchIndexBuilder()
        source = graph()
        builder.add(source)
        before = builder.build()
        source["nodes"][0]["text"] = "Mutated source"
        result = builder.build()
        result["dialogues"][0]["lines"][0][3] = "Mutated result"
        result["dialogues"][0]["speakers"].clear()
        result["dialogues"].clear()
        self.assertEqual(builder.build(), before)

    def test_invalid_addition_is_atomic(self):
        builder = SearchIndexBuilder()
        builder.add(graph())
        before = builder.build()
        with self.assertRaises(ValueError):
            builder.add(graph("d2", nodes=[node(), node("bad", text=42)]))
        self.assertEqual(builder.build(), before)


if __name__ == "__main__":
    unittest.main()
