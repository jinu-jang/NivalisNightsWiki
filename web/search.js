const spokenTypes = new Set(["line", "choice"]);
const apostrophes = /[\u2018\u2019\u02bc\uff07]/gu;
const marks = /\p{M}/gu;
const replacements = { "ß": "ss", "æ": "ae", "œ": "oe", "ø": "o", "ł": "l", "đ": "d", "ς": "σ" };

export class ConversationSearchError extends Error {
  constructor(message, kind = "schema", cause) {
    super(message, cause ? { cause } : undefined);
    this.name = "ConversationSearchError";
    this.kind = kind;
  }
}

function fold(character) {
  return character.replace(apostrophes, "'").normalize("NFKD").toLowerCase().replace(marks, "").replace(/[ßæœøłđς]/gu, item => replacements[item]);
}

function normalize(text) {
  return fold(text).replace(/\s+/gu, " ");
}

function normalizedOffsets(text) {
  let normalized = "", offset = 0;
  const starts = [], ends = [];
  for (const character of text) {
    const start = offset;
    offset += character.length;
    const folded = fold(character);
    if (!folded && ends.length) ends[ends.length - 1] = offset;
    for (const part of folded) {
      const value = /\s/u.test(part) ? " " : part;
      if (value === " " && normalized.endsWith(" ")) {
        ends[ends.length - 1] = offset;
        continue;
      }
      normalized += value;
      for (let index = 0; index < value.length; index++) {
        starts.push(start);
        ends.push(offset);
      }
    }
  }
  return { normalized, starts, ends };
}

function rangesFor(text, terms) {
  const { normalized, starts, ends } = normalizedOffsets(text);
  const ranges = [];
  for (const term of terms) {
    let offset = 0, match;
    while ((match = normalized.indexOf(term, offset)) !== -1) {
      ranges.push([starts[match], ends[match + term.length - 1]]);
      offset = match + Math.max(term.length, 1);
    }
  }
  ranges.sort((left, right) => left[0] - right[0] || left[1] - right[1]);
  const merged = [];
  for (const range of ranges) {
    const previous = merged[merged.length - 1];
    if (previous && range[0] <= previous[1]) previous[1] = Math.max(previous[1], range[1]);
    else merged.push(range);
  }
  return merged;
}

function queryTerms(query) {
  if (typeof query !== "string") throw new TypeError("Conversation search query must be a string.");
  const terms = [];
  for (const match of query.matchAll(/["“]([^"”]*)["”]|([^\s"“”]+)/gu)) {
    const term = normalize(match[1] ?? match[2]).trim();
    if (term && !terms.includes(term)) terms.push(term);
  }
  return terms;
}

function isRecord(value) {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function validString(value) {
  if (typeof value !== "string") return false;
  for (const character of value) {
    const codepoint = character.codePointAt(0);
    if (codepoint >= 0xd800 && codepoint <= 0xdfff) return false;
  }
  return true;
}

function validID(value) {
  return validString(value) && Boolean(value.trim()) && !/[\u0000-\u001f\u007f]/u.test(value);
}

function compileIndex(value) {
  if (!isRecord(value) || value.schemaVersion !== 2 || !Array.isArray(value.dialogues)) {
    throw new ConversationSearchError("The conversation search index must use schema version 2 and contain dialogue records.");
  }
  const ids = new Set();
  return value.dialogues.map(document => {
    if (!isRecord(document) || !validID(document.id) || !validString(document.title) ||
        !Array.isArray(document.speakers) || document.speakers.some(speaker => !validString(speaker)) ||
        !Array.isArray(document.lines)) {
      throw new ConversationSearchError("The conversation search index contains an invalid dialogue record.");
    }
    if (ids.has(document.id)) throw new ConversationSearchError(`The conversation search index repeats dialogue ID ${document.id}.`);
    ids.add(document.id);
    const nodes = new Map(), seen = new Set(), speakers = new Set(document.speakers), lines = [];
    for (const row of document.lines) {
      if (!Array.isArray(row) || row.length !== 4 || !validID(row[0]) ||
          !spokenTypes.has(row[1]) || (row[2] !== null && !validString(row[2])) ||
          !validString(row[3]) || !row[3].trim() || row[3].trim() === "~") {
        throw new ConversationSearchError(`The conversation search index contains an invalid spoken line in ${document.id}.`);
      }
      const [nodeId, kind, speaker, text] = row;
      const previous = nodes.get(nodeId);
      if (previous && (previous.kind !== kind || previous.speaker !== speaker)) {
        throw new ConversationSearchError(`The conversation search index contains conflicting node ID ${nodeId}.`);
      }
      nodes.set(nodeId, { kind, speaker });
      const key = JSON.stringify(row);
      if (seen.has(key)) continue;
      seen.add(key);
      if (speaker?.trim()) speakers.add(speaker);
      lines.push({ nodeId, kind, speaker, text, normalized: normalize(text) });
    }
    const metadata = [document.id, document.title, ...speakers].map(normalize).join("\0");
    return {
      id: document.id, title: document.title, speakers: [...speakers], lines, metadata,
      normalizedTitle: normalize(document.title),
      searchable: [metadata, ...lines.map(line => line.normalized)].join("\0")
    };
  });
}

async function defaultLoadJSON(url) {
  let response;
  try { response = await fetch(url); }
  catch (error) { throw new ConversationSearchError("The conversation search index could not be downloaded. Check your connection and try again.", "network", error); }
  if (!response.ok) throw new ConversationSearchError(`The conversation search index could not be loaded (HTTP ${response.status}).`, "network");
  try { return await response.json(); }
  catch (error) { throw new ConversationSearchError("The conversation search index is not valid JSON.", "json", error); }
}

function abortError(signal) {
  return signal?.reason ?? new DOMException("Conversation search was cancelled.", "AbortError");
}

function checkAbort(signal) {
  if (signal?.aborted) throw abortError(signal);
}

function waitFor(promise, signal) {
  if (!signal) return promise;
  return new Promise((resolve, reject) => {
    const abort = () => { signal.removeEventListener("abort", abort); reject(abortError(signal)); };
    signal.addEventListener("abort", abort, { once: true });
    if (signal.aborted) abort();
    promise.then(
      value => { signal.removeEventListener("abort", abort); resolve(value); },
      error => { signal.removeEventListener("abort", abort); reject(error); }
    );
  });
}

function compareIDs(left, right) {
  return left < right ? -1 : left > right ? 1 : 0;
}

export class ConversationSearch {
  // loadJSON receives only the index URL. Empty queries never download data.
  // Ranges are UTF-16 [start, end) offsets into exact text; metadata has no node.
  constructor(indexURL, { loadJSON = defaultLoadJSON } = {}) {
    if (!(typeof indexURL === "string" && indexURL.trim()) && !(indexURL instanceof URL)) {
      throw new TypeError("Conversation search requires an index URL.");
    }
    if (typeof loadJSON !== "function") throw new TypeError("Conversation search loadJSON must be a function.");
    this.indexURL = indexURL;
    this.loadJSON = loadJSON;
    this.index = null;
    this.loading = null;
  }

  async load() {
    if (this.index) return this.index;
    if (!this.loading) {
      // A caller's cancellation must not abort the shared download for other searches.
      this.loading = Promise.resolve().then(() => this.loadJSON(this.indexURL)).then(compileIndex).then(
        index => { this.index = index; this.loading = null; return index; },
        error => { this.loading = null; throw error; }
      );
    }
    return this.loading;
  }

  async search(query, { signal } = {}) {
    checkAbort(signal);
    const terms = queryTerms(query);
    if (!terms.length) return [];
    const index = await waitFor(this.load(), signal);
    checkAbort(signal);
    const hits = [], phrase = terms.join(" ");
    let deadline = performance.now() + 12;
    for (const document of index) {
      checkAbort(signal);
      if (terms.every(term => document.searchable.includes(term))) {
        let best = null;
        for (const line of document.lines) {
          const matched = terms.filter(term => line.normalized.includes(term));
          if (!matched.length) continue;
          const phraseMatch = line.normalized.includes(phrase);
          const score = 300 + matched.length * 120 + (matched.length === terms.length ? 300 : 0) +
            (phraseMatch ? 450 : 0) + (line.normalized.trim() === phrase ? 60 : 0) + 20 / (1 + line.text.length / 80);
          if (!best || score > best.score ||
              (score === best.score && compareIDs(line.nodeId, best.nodeId) < 0) ||
              (score === best.score && line.nodeId === best.nodeId && compareIDs(line.text, best.text) < 0)) {
            best = {
              dialogueId: document.id, nodeId: line.nodeId, text: line.text,
              speaker: line.speaker, kind: line.kind, score, matchedTokenCount: matched.length,
              totalTokenCount: terms.length, matchesElsewhere: matched.length < terms.length
            };
          }
        }
        if (!best) {
          const matchedTokenCount = terms.filter(term => document.normalizedTitle.includes(term)).length;
          best = {
            dialogueId: document.id, nodeId: null, text: document.title, speaker: null,
            kind: "metadata", score: 20 + matchedTokenCount * 5, matchedTokenCount,
            totalTokenCount: terms.length, matchesElsewhere: matchedTokenCount < terms.length
          };
        }
        best.matchRanges = rangesFor(best.text, terms);
        hits.push(best);
      }
      if (performance.now() >= deadline) {
        await waitFor(new Promise(resolve => setTimeout(resolve, 0)), signal);
        checkAbort(signal);
        deadline = performance.now() + 12;
      }
    }
    hits.sort((left, right) => right.score - left.score || compareIDs(left.dialogueId, right.dialogueId));
    return hits;
  }
}
