import { DialogueRoute, displayName, summarizeEffects, meaningfulEffects, describedEffects, nodeEffects, nodeLabel, nodeText, signed } from "./route.js";
import { requirementsText } from "./labels.js";
import { ConversationGraph } from "./graph.js";
import { dataURL, validateMedia, speakerProfile, relationshipChanges } from "./media.js";
import { ConversationSearch } from "./search.js";
import { effectAnalysis, outcomeItems, reachableEffectItems, orderedOutcomes } from "./outcomes.js";

const $ = id => document.getElementById(id);
const make = (tag, className, text) => {
  const element = document.createElement(tag);
  if (className) element.className = className;
  if (text !== undefined) element.textContent = text;
  return element;
};
const catalogURL = new URL("data/catalog.json", document.baseURI);
const pageSize = 24;
const state = {
  catalog: null, collection: "quests", page: 0, search: "", quest: null, dialogue: null,
  questId: null, dialogueId: null, objective: null, route: null, routeRows: 20, request: 0,
  controller: null, retry: null, view: "conversation", expanded: false, inspectedNode: null,
  media: null, mediaURL: null, outcomeRows: 12, comparedOutcome: null,
  searchEngine: null, searchController: null, searchRequest: 0, searchTimer: null
};
const questCache = new Map(), dialogueCache = new Map();
const imageLoads = new Map();
class GuideDataError extends Error {
  constructor(kind, message) { super(message); this.kind = kind; }
}
const graph = new ConversationGraph($("graph"), $("graph-viewport"), {
  onInspect: inspectNode,
  getArtwork: name => {
    const artwork = state.media?.relationships[name];
    return artwork ? dataURL(artwork.file, state.mediaURL, catalogURL) : null;
  },
  onZoom: percent => { const label = percent < 1 ? "<1%" : `${percent}%`; $("zoom-level").value = label; $("zoom-level").textContent = label; }
});

function originalIcon(name) {
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("class", "icon"); svg.setAttribute("aria-hidden", "true");
  const use = document.createElementNS("http://www.w3.org/2000/svg", "use");
  use.setAttribute("href", `assets/icons.svg#${name}`);
  svg.append(use);
  return svg;
}

function warnMedia(message) {
  $("media-warning").textContent = message;
  $("media-warning").hidden = false;
}

function loadImage(url) {
  const key = url.href;
  if (!imageLoads.has(key)) imageLoads.set(key, new Promise(resolve => {
    const image = new Image();
    image.onload = () => resolve(true);
    image.onerror = () => resolve(false);
    image.src = key;
  }));
  return imageLoads.get(key);
}

function icon(name) {
  const artwork = state.media?.relationships[name] || (name === "quest" ? state.media?.ui?.questBadge : null);
  if (!artwork) return originalIcon(name);
  const url = dataURL(artwork.file, state.mediaURL, catalogURL);
  const span = make("span", "icon game-icon");
  span.setAttribute("aria-hidden", "true");
  span.dataset.mediaRole = name;
  span.style.setProperty("--icon-image", `url("${url.href}")`);
  loadImage(url).then(loaded => {
    if (!loaded) {
      span.replaceWith(originalIcon(name));
      warnMedia("A game symbol could not be loaded. An original wiki symbol is shown instead; re-export the artwork pack to restore it.");
    }
  });
  return span;
}

function renderArtworkTheme() {
  const emblem = document.querySelector(".quest-emblem");
  const replacement = icon("quest");
  replacement.classList.add("quest-emblem");
  emblem.replaceWith(replacement);
  const frame = document.querySelector(".speech-frame");
  const bubble = state.media?.ui?.dialogueBubble;
  frame.classList.remove("has-game-frame");
  frame.style.removeProperty("border-image-source");
  frame.style.removeProperty("border-image-slice");
  if (bubble) {
    const { top, right, bottom, left } = bubble.border;
    const url = dataURL(bubble.file, state.mediaURL, catalogURL);
    frame.style.borderImageSource = `url("${url.href}")`;
    frame.style.borderImageSlice = `${top} ${right} ${bottom} ${left} fill`;
    frame.classList.add("has-game-frame");
    loadImage(url).then(loaded => {
      if (!loaded && frame.style.borderImageSource.includes(url.href)) {
        frame.style.removeProperty("border-image-source");
        frame.style.removeProperty("border-image-slice");
        frame.classList.remove("has-game-frame");
        warnMedia("The game's dialogue frame could not be loaded. The wiki frame is shown instead; re-export the artwork pack to restore it.");
      }
    });
  }
}

function effectElement(item, compact = true) {
  const span = make("span", `effect ${item.icon}`);
  span.dataset.effectKind = item.kind || "outcome";
  if (item.variable) span.dataset.variable = item.variable;
  if (item.value !== undefined) span.dataset.value = String(item.value);
  if (item.recipe) span.dataset.recipe = item.recipe;
  const profile = speakerProfile(state.media, state.route?.current);
  const contextual = compact && profile?.variableName && item.variable?.startsWith(`${profile.variableName}.`) && item.kind === "delta";
  span.append(icon(item.icon), make("span", "", contextual ? signed(item.value) : item.text));
  span.setAttribute("aria-label", item.text);
  span.title = [item.text, item.variable ? `Source variable: ${item.variable}` : "", item.internal ? "Internal state change; gameplay consequences are not inferred." : ""].filter(Boolean).join("\n");
  if (item.icon === "enemy") span.title += "\nEnemy is an internal variable name; exact in-game UI meaning is unconfirmed.";
  else if (item.icon === "romance") span.title += "\nRomance is an internal relationship variable.";
  return span;
}

function addEffects(holder, summary, emptyText = "", includeZero = false, includeInternal = false) {
  const items = includeInternal ? describedEffects(summary, includeZero) : meaningfulEffects(summary, includeZero);
  holder.replaceChildren(...items.map(item => effectElement(item)));
  if (!holder.childElementCount && emptyText) holder.append(make("p", "route-empty", emptyText));
}

function resourceURL(metadata) {
  return dataURL(metadata?.file, catalogURL, catalogURL);
}

async function readJSON(url, signal) {
  let response;
  try { response = await fetch(url, { signal }); }
  catch (error) {
    if (error.name === "AbortError") throw error;
    throw new GuideDataError("network", "The browser could not reach the requested data file. Check the data pack and local HTTP server, then reload.");
  }
  if (!response.ok) throw new GuideDataError("http", `The requested data file could not be loaded (HTTP ${response.status}).`);
  try { return await response.json(); }
  catch (error) {
    if (error.name === "AbortError") throw error;
    throw new GuideDataError("malformed", "The requested data file is not valid JSON. Re-export or replace the data pack, then reload.");
  }
}

async function readRecord(metadata, cache, signal) {
  if (cache.has(metadata.id)) return cache.get(metadata.id);
  const value = await readJSON(resourceURL(metadata), signal);
  if (!value || value.schemaVersion !== 2) throw new GuideDataError("schema", "This data uses an unsupported schema. Export a schema version 2 pack and retry.");
  cache.set(metadata.id, value);
  return value;
}

function showStatus(title, message, retry = null, { kind = retry ? "error" : "loading", packGuide = false, detail = "" } = {}) {
  $("page-status").hidden = false;
  $("page-status").classList.toggle("is-error", kind === "error");
  $("page-status").classList.toggle("is-empty", kind === "empty");
  $("page-status").setAttribute("aria-live", kind === "error" ? "assertive" : "polite");
  $("guide-content").hidden = true;
  $("status-title").textContent = title;
  $("status-message").textContent = message;
  $("retry").hidden = !retry;
  $("retry").textContent = packGuide ? "Reload library" : "Try again";
  $("data-pack-guide").hidden = !packGuide;
  $("status-diagnostics").hidden = !detail;
  $("status-diagnostics").open = false;
  $("status-detail").textContent = detail;
  state.retry = retry;
  $("main").setAttribute("aria-busy", kind === "loading" ? "true" : "false");
}

function showEmptyLibrary() {
  document.title = "Nivalis Nights Wiki — Add a local reference pack";
  showStatus("The city is waiting.", "Your wiki is ready. This edition has no quest or conversation data loaded yet.", bootstrap, { kind: "empty", packGuide: true });
}

function paramsFromHash() {
  const params = new URLSearchParams(location.hash.slice(1));
  return { quest: params.get("quest"), dialogue: params.get("dialogue"), objective: params.get("objective"), node: params.get("node") };
}

function setHash(params, replace = false) {
  const query = new URLSearchParams();
  for (const key of ["quest", "dialogue", "objective", "node"]) if (params[key]) query.set(key, params[key]);
  const hash = `#${query}`;
  if (location.hash !== hash) history[replace ? "replaceState" : "pushState"](null, "", hash);
}

function navigate(params) {
  setHash(params);
  closeLibrary(false);
  loadRoute(params);
  $("main").focus({ preventScroll: true });
}

async function bootstrap() {
  showStatus("Opening the wiki…", "Loading the quest and conversation library.");
  $("media-warning").hidden = true;
  $("library-search").disabled = true;
  try {
    const catalog = await readJSON(catalogURL);
    if (!catalog || catalog.schemaVersion !== 2 || !Array.isArray(catalog.quests) || !Array.isArray(catalog.dialogues) || [...catalog.quests, ...catalog.dialogues].some(item => !item || typeof item.id !== "string" || typeof item.file !== "string")) throw new GuideDataError("schema", "The library requires a schema version 2 catalog containing valid quest and conversation records.");
    state.catalog = catalog;
    state.searchEngine = catalog.searchFile ? new ConversationSearch(resourceURL({ file: catalog.searchFile }), { loadJSON: readJSON }) : null;
    state.media = null; state.mediaURL = null;
    if (catalog.mediaFile) {
      state.mediaURL = resourceURL({ file: catalog.mediaFile });
      state.media = validateMedia(await readJSON(state.mediaURL));
    }
    renderArtworkTheme();
    const coverage = catalog.coverage || {};
    $("coverage").textContent = `${coverage.questsExported ?? catalog.quests.length} of ${coverage.questsFound ?? catalog.quests.length} quests · ${coverage.dialoguesExported ?? catalog.dialogues.length} of ${coverage.dialoguesFound ?? catalog.dialogues.length} conversations exported. Coverage is not a claim of a complete game walkthrough.`;
    renderLibrary();
    if (!catalog.quests.length && !catalog.dialogues.length) {
      showEmptyLibrary();
      return;
    }
    $("library-search").disabled = false;
    const saved = paramsFromHash();
    const params = saved.quest || saved.dialogue ? saved : { quest: catalog.defaultQuestId || catalog.quests[0]?.id, dialogue: catalog.defaultDialogueId || (!catalog.quests.length ? catalog.dialogues[0]?.id : null) };
    if (!params.quest && !params.dialogue) {
      showEmptyLibrary();
      return;
    }
    await loadRoute(params, true);
  } catch (error) {
    const malformed = ["malformed", "schema"].includes(error.kind);
    document.title = "Nivalis Nights Wiki — Library unavailable";
    showStatus(malformed ? "This data pack needs attention." : "The library couldn’t be opened.", malformed ? "The library file is unreadable or uses a different format. Re-export or replace the pack, then reload." : "The data pack is missing or couldn’t be reached. Add a local pack or an approved published pack, then reload.", bootstrap, { kind: "error", packGuide: true, detail: error.message || "The catalog could not be read. Check that its records follow the version 2 schema." });
    $("library-count").textContent = "Library unavailable";
    $("library-results").replaceChildren(make("p", "library-empty", "Your quests and conversations will appear here once a data pack is available."));
    $("library-pagination").hidden = true;
  }
}

async function loadRoute(params, replace = false) {
  if (!state.catalog) return;
  if (!state.catalog.quests.length && !state.catalog.dialogues.length) { showEmptyLibrary(); return; }
  state.controller?.abort();
  const controller = new AbortController();
  state.controller = controller;
  const request = ++state.request;
  showStatus("Opening the reference…", "Loading the selected quest and conversation.");
  try {
    let quest = null, objective = null, dialogue = null;
    if (params.dialogue && !state.catalog.dialogues.some(item => item.id === params.dialogue)) throw new Error("That conversation isn’t in this exported library. Choose another conversation from the sidebar.");
    const questMetadata = params.quest ? state.catalog.quests.find(item => item.id === params.quest) : null;
    if (params.quest && !questMetadata) throw new Error("That quest isn’t in this exported library. Choose another quest from the sidebar.");
    if (questMetadata) {
      quest = await readRecord(questMetadata, questCache, controller.signal);
      if (!Array.isArray(quest.objectives)) throw new Error("The quest file has no valid objectives collection.");
      const requestedObjective = params.objective ? quest.objectives.find(item => item.id === params.objective) : null;
      if (params.objective && !requestedObjective) throw new Error("That objective isn’t part of this quest. Choose the quest again from the library.");
      objective = requestedObjective || quest.objectives.find(item => (item.dialogueIds || []).includes(params.dialogue)) || quest.objectives[0] || null;
      if (params.dialogue && ![...(quest.dialogueIds || []), ...quest.objectives.flatMap(item => item.dialogueIds || [])].includes(params.dialogue)) throw new Error("That conversation isn’t linked to this quest. Choose a quest objective or open the conversation from the library.");
    }
    const availableIds = quest ? (objective ? objective.dialogueIds || [] : quest.dialogueIds || []) : [];
    let dialogueId = quest ? (availableIds.includes(params.dialogue) ? params.dialogue : availableIds.includes(state.catalog.defaultDialogueId) ? state.catalog.defaultDialogueId : availableIds[0]) : params.dialogue;
    // A dialogue explicitly selected from the quest-level picker may not belong to an objective.
    if (quest && !params.objective && params.dialogue && (quest.dialogueIds || []).includes(params.dialogue) && !quest.objectives.some(item => (item.dialogueIds || []).includes(params.dialogue))) {
      objective = null; dialogueId = params.dialogue;
    }
    const dialogueMetadata = dialogueId ? state.catalog.dialogues.find(item => item.id === dialogueId) : null;
    if (dialogueId && !dialogueMetadata) throw new Error("This linked conversation wasn’t exported. No dialogue content has been inferred; choose another objective or library record.");
    if (dialogueMetadata) {
      dialogue = await readRecord(dialogueMetadata, dialogueCache, controller.signal);
      if (!Array.isArray(dialogue.nodes) || !Array.isArray(dialogue.edges)) throw new Error("The conversation file has no valid nodes and edges collections.");
    }
    if (params.node && !dialogue?.nodes.some(node => node.id === params.node)) throw new Error("That node is not part of this conversation. Open the conversation from the library to inspect its available nodes.");
    if (controller.signal.aborted || request !== state.request) return;
    state.quest = quest; state.questId = questMetadata?.id || null;
    state.objective = objective; state.dialogue = dialogue; state.dialogueId = dialogueMetadata?.id || null;
    state.route = dialogue?.nodes.length ? new DialogueRoute(dialogue) : null;
    state.routeRows = 20;
    state.outcomeRows = 12; state.comparedOutcome = null;
    state.inspectedNode = null;
    $("workspace").classList.toggle("is-expanded", state.expanded);
    $("expand-map").setAttribute("aria-pressed", String(state.expanded));
    $("expand-map").setAttribute("aria-label", state.expanded ? "Return to conversation and map" : "Expand conversation map");
    $("map-inspection").hidden = true;
    setView(params.node ? "map" : state.view, false);
    renderGuide();
    renderLibrary();
    setHash({ quest: state.questId, dialogue: state.dialogueId, objective: state.objective?.id, node: params.node }, replace);
    $("page-status").hidden = true;
    $("guide-content").hidden = false;
    $("main").setAttribute("aria-busy", "false");
    window.scrollTo({ top: 0, behavior: "auto" });
    if (request > 1) { $("guide-title").tabIndex = -1; $("guide-title").focus({ preventScroll: true }); }
    if (state.route) {
      graph.update(state.route.state);
      if (params.node) {
        inspectNode(state.route.nodes.get(params.node));
        $("map-inspection-title").textContent = `Linked node \u00b7 ${nodeLabel(state.inspectedNode)}`;
        requestAnimationFrame(() => graph.focusNode(params.node));
      }
    }
  } catch (error) {
    if (error.name === "AbortError" || request !== state.request) return;
    showStatus("This reference couldn’t be opened", error instanceof TypeError || error instanceof SyntaxError ? "This reference is incomplete or unreadable. Re-export or replace the data pack, or choose another record." : error.message, () => loadRoute(params), { detail: error instanceof GuideDataError ? error.message : "" });
  }
}

function appendSnippet(holder, hit) {
  const text = hit.text, first = hit.matchRanges[0]?.[0] || 0;
  let start = Math.max(0, first - 35), end = Math.min(text.length, start + 170);
  if (start && /[\uDC00-\uDFFF]/.test(text[start])) start--;
  if (end < text.length && /[\uDC00-\uDFFF]/.test(text[end])) end++;
  const snippet = make("span", "search-snippet");
  if (start) snippet.append(document.createTextNode("\u2026"));
  let cursor = start;
  for (const [from, to] of hit.matchRanges) {
    const left = Math.max(start, from), right = Math.min(end, to);
    if (right <= left) continue;
    snippet.append(document.createTextNode(text.slice(cursor, left)), make("mark", "", text.slice(left, right)));
    cursor = right;
  }
  snippet.append(document.createTextNode(text.slice(cursor, end) + (end < text.length ? "\u2026" : "")));
  holder.append(make("small", "search-speaker", hit.kind === "choice" ? "Your reply" : displayName(hit.speaker, "Spoken line")), snippet);
  if (hit.matchesElsewhere) holder.append(make("small", "", "Other search terms match elsewhere in this conversation."));
}

async function renderLibrary() {
  if (!state.catalog) return;
  clearTimeout(state.searchTimer);
  state.searchController?.abort();
  const controller = new AbortController(), request = ++state.searchRequest;
  state.searchController = controller;
  const questMode = state.collection === "quests";
  const collection = state.catalog[state.collection];
  const query = state.search.trim(), hits = new Map();
  const holder = $("library-results");
  holder.setAttribute("aria-busy", "false");
  $("search-hint").textContent = questMode ? "Quest names, descriptions and quest givers. Use Dialogue for spoken lines." : state.searchEngine ? "Search spoken lines, replies, characters or titles. Quotes match a phrase." : "Older pack: only titles and characters are searchable. Re-export to search spoken lines.";
  $("library-search").placeholder = questMode ? "Quest or person\u2026" : "A line you remember\u2026";
  const tokens = state.search.toLocaleLowerCase().trim().split(/\s+/).filter(Boolean);
  let filtered;
  if (!questMode && query && state.searchEngine) {
    holder.setAttribute("aria-busy", "true");
    holder.replaceChildren(make("p", "library-empty", "Searching conversations\u2026"));
    $("library-count").textContent = "Searching\u2026";
    $("library-pagination").hidden = true;
    try {
      const results = await state.searchEngine.search(query, { signal: controller.signal });
      if (request !== state.searchRequest || controller.signal.aborted) return;
      const records = new Map(collection.map(item => [item.id, item]));
      filtered = results.map(hit => {
        const record = records.get(hit.dialogueId);
        if (!record) throw new GuideDataError("schema", "The search index and conversation catalog do not match. Re-export the data pack.");
        hits.set(hit.dialogueId, hit); return record;
      });
    } catch (error) {
      if (controller.signal.aborted || request !== state.searchRequest) return;
      holder.setAttribute("aria-busy", "false");
      holder.replaceChildren(make("p", "library-empty", `Search unavailable. ${error.message}`));
      const retry = make("button", "search-retry", "Retry search");
      retry.type = "button"; retry.addEventListener("click", renderLibrary);
      holder.append(retry); $("library-count").textContent = "Search unavailable";
      return;
    }
  } else filtered = collection.filter(item => {
    const haystack = [item.title, item.description, item.owner, ...(item.speakers || [])].filter(Boolean).join(" ").toLocaleLowerCase();
    return tokens.every(token => haystack.includes(token));
  });
  holder.setAttribute("aria-busy", "false");
  const pages = Math.max(1, Math.ceil(filtered.length / pageSize));
  state.page = Math.max(0, Math.min(state.page, pages - 1));
  const start = state.page * pageSize;
  holder.replaceChildren();
  holder.setAttribute("aria-label", questMode ? "Quests" : "Conversations");
  holder.setAttribute("aria-labelledby", questMode ? "quests-tab" : "dialogues-tab");
  for (const item of filtered.slice(start, start + pageSize)) {
    const button = make("button", "library-item");
    button.type = "button";
    button.dataset.recordId = item.id;
    button.dataset.recordKind = questMode ? "quest" : "dialogue";
    button.append(make("strong", "", displayName(item.title, questMode ? "Untitled quest" : "Untitled conversation")));
    const secondary = questMode ? [displayName(item.owner, ""), `${item.objectiveCount || 0} objectives`].filter(Boolean).join(" · ") : (item.speakers || []).map(name => displayName(name, "")).filter(Boolean).slice(0, 2).join(" · ") || "Conversation";
    button.append(make("small", "", secondary));
    const hit = hits.get(item.id);
    if (hit?.nodeId) { button.dataset.nodeId = hit.nodeId; appendSnippet(button, hit); }
    if (item.id === (questMode ? state.questId : state.dialogueId)) button.setAttribute("aria-current", "page");
    button.addEventListener("click", () => navigate(questMode ? { quest: item.id } : { dialogue: item.id, node: hit?.nodeId }));
    holder.append(button);
  }
  if (!filtered.length) holder.append(make("p", "library-empty", state.search ? questMode ? "No matching quests. Try a different title or person." : "No matching conversations. Try fewer words or a different line." : !collection.length ? "Wiki references will appear here when a data pack is added." : "Nothing in this collection has been exported."));
  $("library-count").textContent = filtered.length ? `${start + 1}–${Math.min(start + pageSize, filtered.length)} of ${filtered.length} ${questMode ? "quests" : "conversations"}` : state.search ? "No results" : "No references loaded";
  $("library-pagination").hidden = pages <= 1;
  $("library-page").textContent = `${state.page + 1} / ${pages}`;
  $("library-prev").disabled = state.page === 0;
  $("library-next").disabled = state.page === pages - 1;
}

function renderGuide() {
  const { quest, dialogue, objective } = state;
  const title = displayName(quest?.title || dialogue?.title, quest ? "Untitled quest" : "Untitled conversation");
  $("guide-title").textContent = title;
  document.title = `${title} — Nivalis Nights Wiki`;
  $("quest-description").textContent = quest?.description || "";
  $("quest-description").hidden = !$("quest-description").textContent;
  $("quest-owner").textContent = quest?.owner ? `With ${displayName(quest.owner, "an unknown quest giver")}` : "";
  $("quest-owner").hidden = !$("quest-owner").textContent;
  $("quest-overview").hidden = !quest;
  $("quest-overview").open = false;
  $("overview-count").textContent = `${quest?.objectives.length || 0} objectives`;
  const breadcrumbs = $("breadcrumbs");
  breadcrumbs.replaceChildren(make("span", "", "Wiki"), make("span", "", "/"));
  const collectionButton = make("button", "", quest ? "Quest library" : "Conversations");
  collectionButton.type = "button";
  collectionButton.addEventListener("click", () => { selectCollection(quest ? "quests" : "dialogues"); if (window.matchMedia("(max-width: 700px)").matches) openLibrary(); else $("library-search").focus(); });
  breadcrumbs.append(collectionButton, make("span", "", "/"), make("span", "", title));
  for (const separator of breadcrumbs.querySelectorAll("span")) if (separator.textContent === "/") separator.setAttribute("aria-hidden", "true");
  $("objective-navigation").hidden = !quest?.objectives.length;
  $("objective-list").replaceChildren();
  const objectives = (quest?.objectives || []).map((item, index) => ({ item, order: Number.isFinite(item.sourceOrder) ? item.sourceOrder : index })).sort((a, b) => a.order - b.order).map(({ item }) => item);
  for (const item of objectives) {
    const button = make("button");
    button.type = "button";
    button.dataset.objectiveId = item.id;
    if (Number.isFinite(item.sourceOrder)) button.dataset.sourceOrder = String(item.sourceOrder);
    button.append(make("strong", "", displayName(item.title || item.targetName, "Untitled objective")));
    if (item.id === objective?.id) button.setAttribute("aria-current", "true");
    button.addEventListener("click", () => navigate({ quest: state.questId, objective: item.id, dialogue: (item.dialogueIds || [])[0] }));
    $("objective-list").append(button);
  }
  $("objective-context").hidden = !objective || (!objective.description && !(objective.onStart || []).length && !(objective.onComplete || []).length);
  $("objective-description").textContent = objective?.description || "";
  $("objective-effects").replaceChildren();
  for (const [label, effects] of [["On objective start", objective?.onStart || []], ["On objective completion", objective?.onComplete || []]]) {
    const meaningful = meaningfulEffects(summarizeEffects(effects));
    if (!meaningful.length) continue;
    const row = make("div", "effect-row");
    row.append(make("span", "", `${label}:`), ...meaningful.map(effectElement));
    $("objective-effects").append(row);
  }
  $("dialogue-title").textContent = dialogue ? displayName(dialogue.title, "Conversation") : "Quest objective";
  $("dialogue-subtitle").textContent = dialogue ? "Choose a reply. Watch the map follow your path." : "";
  const ids = quest ? [...new Set([...(objective?.dialogueIds || []), ...(quest.dialogueIds || [])])] : [];
  const records = ids.map(id => state.catalog.dialogues.find(item => item.id === id)).filter(Boolean);
  $("dialogue-picker").hidden = records.length < 2;
  $("dialogue-select").replaceChildren();
  if (!dialogue && records.length) {
    const placeholder = make("option", "", "Other quest conversations");
    placeholder.value = ""; placeholder.selected = true; placeholder.disabled = true;
    $("dialogue-select").append(placeholder);
  }
  for (const record of records) {
    const option = make("option", "", displayName(record.title, "Untitled conversation"));
    option.value = record.id; option.selected = record.id === state.dialogueId;
    $("dialogue-select").append(option);
  }
  const hasRoute = Boolean(state.route);
  $("outcome-summary").hidden = !hasRoute;
  $("workspace").hidden = !hasRoute;
  $("mobile-views").hidden = !hasRoute;
  $("task-state").hidden = hasRoute;
  if (hasRoute) {
    const layout = graph.load(dialogue);
    $("graph-status").textContent = `${dialogue.nodes.length} nodes · ${dialogue.edges.length} links.${layout.hasCycles ? " Loops are shown; automatic loops pause for your decision." : ""} Scroll to explore; node selection only inspects.`;
    renderPossibleChanges();
    renderConversation();
  } else {
    $("task-title").textContent = dialogue ? "No conversation nodes were exported" : objective ? displayName(objective.title || objective.targetName, "A task outside conversation") : "No linked conversation";
    $("task-message").textContent = dialogue ? "This conversation record is empty. Its source and extraction warnings are available in Research notes." : objective ? `${objective.description || (objective.targetName ? `Target: ${displayName(objective.targetName)}` : "This objective is not linked to a dialogue.")}\n\nThis is a quest task, not a conversation. No missing dialogue link has been invented; progress and completion are not simulated.` : "This quest has no exported dialogue link for the selected reference. Browse its objectives or choose another quest.";
  }
  renderResearch();
}

function renderPossibleChanges() {
  const holder = $("possible-changes");
  holder.replaceChildren();
  const analysis = effectAnalysis(state.dialogue), count = analysis.outcomes.length;
  const effects = count ? outcomeItems(analysis) : reachableEffectItems(state.dialogue);
  $("outcome-title").textContent = count ? "Possible outcomes" : "Changes found";
  $("outcome-count").textContent = `${count} effect combination${count === 1 ? "" : "s"}${analysis.status === "complete" ? "" : ` \u00b7 ${analysis.status} analysis`}`;
  holder.append(...effects.slice(0, 8).map(item => effectElement(item, false)));
  if (!effects.length) holder.append(make("p", "", count ? "No decoded relationship, reward or state changes on the analyzed routes." : "No decoded effects were found within this conversation."));
  if (effects.length > 8) holder.append(make("span", "muted", `+${effects.length - 8} more in the combinations below`));
  const common = count && analysis.status === "complete" ? effects.filter(item => item.kind === "unlock_recipe" && analysis.outcomes.every(outcome => outcome.summary.recipes.includes(item.recipe))).map(item => displayName(item.recipe)) : [];
  $("outcome-note").textContent = [
    common.length ? `${common.join(", ")} unlocks on every analyzed combination.` : "",
    count ? "Ranges belong to separate routes; their maxima may not be achievable together." : "Individual changes are shown, not a proven connected outcome.",
    analysis.status !== "complete" ? analysis.message : "",
    "Requirements are not checked against your save."
  ].filter(Boolean).join(" ");
  $("combination-title").textContent = `Compare ${count} effect combination${count === 1 ? "" : "s"}`;
  $("outcome-combinations").hidden = !count;
  renderOutcomeCombinations();
}

function setComparison(outcome) {
  state.comparedOutcome = outcome?.id || null;
  graph.compare(outcome?.witness);
  $("clear-comparison").hidden = !outcome;
  for (const button of $("combination-list").querySelectorAll("[data-outcome-id]")) button.setAttribute("aria-pressed", String(button.dataset.outcomeId === state.comparedOutcome));
  if (outcome) {
    setView("map");
    requestAnimationFrame(() => {
      graph.focusNode(outcome.witness.choices[0] || outcome.witness.nodes[0]);
      $("graph-viewport").focus({ preventScroll: true });
      const pane = $("graph-pane"), bounds = pane.getBoundingClientRect();
      if (bounds.top < 0 || bounds.bottom > innerHeight) pane.scrollIntoView({ block: "start", behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth" });
    });
    $("route-announcement").textContent = "Compared path highlighted on the map. Your explored route has not changed.";
  }
}

function renderOutcomeCombinations() {
  const analysis = effectAnalysis(state.dialogue), outcomes = orderedOutcomes(analysis), holder = $("combination-list");
  holder.replaceChildren();
  for (const [index, outcome] of outcomes.slice(0, state.outcomeRows).entries()) {
    const row = make("div", "outcome-combination");
    row.dataset.combinationId = outcome.id;
    const heading = make("div", "combination-heading");
    heading.append(make("h4", "", `Combination ${index + 1}`));
    if (outcome.witness) {
      const button = make("button", "compare-outcome", "Show path on map");
      button.type = "button"; button.dataset.outcomeId = outcome.id;
      button.setAttribute("aria-pressed", String(state.comparedOutcome === outcome.id));
      button.addEventListener("click", () => setComparison(state.comparedOutcome === outcome.id ? null : outcome));
      heading.append(button);
    }
    const effects = make("div", "effect-row");
    addEffects(effects, outcome.summary, "No decoded effect changes.", false, true);
    row.append(heading, effects);
    if (outcome.conditions.length) row.append(make("p", "combination-requirements", `Requires: ${requirementsText(outcome.conditions)}`));
    const uncertain = (outcome.summary.unparsed || []).length || (outcome.summary.actions || []).length;
    if (uncertain) row.append(make("p", "combination-caution", "Script or action intent is retained, not executed. Decoded totals may be incomplete."));
    if (outcome.witness) {
      const choices = make("details", "combination-choices");
      choices.append(make("summary", "", `${outcome.witness.choices.length} replies on this example path`));
      const list = make("ol");
      for (const id of outcome.witness.choices) list.append(make("li", "", state.route.nodes.get(id)?.text || id));
      choices.append(list); row.append(choices);
    }
    holder.append(row);
  }
  $("more-combinations").hidden = state.outcomeRows >= outcomes.length;
  $("clear-comparison").hidden = !state.comparedOutcome;
}

function choiceLabel(edge, index, total) {
  const target = state.route.nodes.get(edge.to);
  if (target?.type === "choice") return target.text || "Use this reply (localized text unavailable)";
  if (state.route.state.stop === "reply") return total > 1 ? `Use this reply · ${edge.label || `branch ${index + 1}`}` : "Use this reply";
  if (edge.label) return `Explore branch · ${edge.label}`;
  if (total === 1 && state.route.state.stop === "cycle") return "Explore the next step in this loop";
  if (target?.type === "exit") return "Finish this conversation";
  if (["external", "subdialogue"].includes(target?.type)) return "Inspect the linked conversation context";
  if (total === 1 && state.route.state.stop !== "gate") return "Continue conversation";
  if (target?.type === "line") return `Explore branch ${index + 1} · ${nodeLabel(target)}: ${target.text || "next reply"}`;
  if (target?.type === "action") return `Explore branch ${index + 1} · ${nodeText(target)}`;
  if (target?.type === "condition") return `Explore branch ${index + 1} · ${nodeText(target)}`;
  return `Explore branch ${index + 1} · ${target ? nodeLabel(target) : "unexported continuation"}`;
}

function renderConversation(announce = false) {
  const route = state.route, node = route.current;
  const stop = route.state.stop;
  $("speaker").textContent = stop === "exit" ? "Conversation complete" : stop === "missing" ? "An unexported continuation" : nodeLabel(node);
  $("speaker").tabIndex = -1;
  $("demeanor").textContent = node?.type === "line" && node.demeanor?.name && !["Unspecified", "Unknown"].includes(node.demeanor.name) ? node.demeanor.name : "";
  $("npc-quote").textContent = stop === "exit" ? "End of this conversation." : nodeText(node);
  $("npc-quote").classList.toggle("system-quote", node?.type !== "line" && node?.type !== "choice");
  let note = "";
  if (stop === "exit") note = "You’ve reached this conversation’s exit. The totals below describe this explored route; they do not change your game. Any outgoing links are context, not instructions to continue automatically.";
  else if (["external", "subdialogue"].includes(stop)) note = "This link leads outside the extracted conversation. Exploration stops here; linked game instructions are not executed.";
  else if (stop === "missing") note = "The source refers to a node that wasn’t exported. Go back or restart; no continuation has been invented.";
  else if (stop === "branch") note = "The source contains more than one automatic continuation. Pick a branch to explore; the guide won’t select one for you.";
  else if (stop === "gate") note = "The next branch depends on a game requirement. You can inspect either path; this wiki does not check your save.";
  else if (stop === "dead_end" || !route.alternatives.length) note = "No outgoing continuation was exported for this node. This is not a confirmed complete route.";
  $("conversation-note").textContent = note;
  const routeSummary = summarizeEffects(route.state.effects);
  renderSpeaker(routeSummary);
  $("route-snapshot").hidden = !describedEffects(routeSummary, true).length;
  addEffects($("route-stats"), routeSummary, "", true, true);
  const alternatives = route.alternatives;
  const replyChoices = alternatives.length && alternatives.every(edge => route.nodes.get(edge.to)?.type === "choice");
  $("choice-heading").textContent = stop === "exit" ? "A route worth noting" : replyChoices ? "Choose your reply" : alternatives.length > 1 || ["gate", "cycle"].includes(stop) ? "Choose a branch to explore" : stop === "reply" ? "Your reply" : "Next in the conversation";
  $("choice-count").textContent = alternatives.length > 1 ? `${alternatives.length} options` : "";
  $("choices").replaceChildren();
  for (const [index, edge] of alternatives.entries()) {
    const preview = route.preview(edge);
    const button = make("button", "choice");
    button.type = "button";
    button.dataset.edgeId = edge.id;
    button.dataset.targetId = edge.to;
    button.append(make("span", "choice-number", String(index + 1)));
    const copy = make("span", "choice-copy");
    copy.append(make("span", "choice-text", choiceLabel(edge, index, alternatives.length)));
    const outcomes = make("span", "choice-preview");
    addEffects(outcomes, preview.summary, "", false, true);
    copy.append(outcomes);
    const requirements = requirementsText([...(node?.conditions || []), ...preview.conditions]);
    if (requirements) copy.append(make("span", "gate-hint", `Requires: ${requirements}`));
    else if (preview.stop === "gate" || stop === "gate" || node?.type === "condition") copy.append(make("span", "gate-hint", "A further game requirement controls this path; select it to inspect the rule."));
    else if (["branch", "cycle"].includes(preview.stop)) copy.append(make("span", "gate-hint", "More branches ahead · preview stops before a decision"));
    const arrow = icon("arrow"); arrow.classList.add("choice-arrow");
    button.append(copy, arrow);
    button.addEventListener("click", () => { if (route.choose(edge.id)) { renderConversation(true); $("speaker").focus({ preventScroll: true }); } });
    $("choices").append(button);
  }
  if (stop === "exit") $("choices").append(make("p", "branch-note", "Step back to try a different reply, or start again to compare routes."));
  const warning = stop === "cycle" ? "An automatic loop has been paused. Continue deliberately or go back. Revisiting a node will count its effects again." : route.state.conditions.length ? `Requirements encountered: ${requirementsText(route.state.conditions)}. These have not been checked against your save.` : stop === "gate" ? "This is a conditional branch. Its rule is shown on the map; availability in your game is unknown." : "";
  $("route-warning").textContent = warning;
  $("route-warning").hidden = !warning;
  $("back").disabled = !route.canBack;
  graph.update(route.state);
  refreshOpenResearch();
  if (announce) $("route-announcement").textContent = `${$("speaker").textContent}. ${node?.type === "line" ? node.text : note} ${alternatives.length ? `${alternatives.length} continuation options.` : ""}`;
}

function renderSpeaker(summary) {
  const node = state.route.current;
  const profile = speakerProfile(state.media, node);
  const portrait = profile?.portrait;
  const image = $("speaker-portrait");
  $("portrait-frame").hidden = !portrait;
  $("portrait-status").hidden = true;
  image.hidden = false;
  if (portrait) {
    const url = dataURL(portrait.file, state.mediaURL, catalogURL);
    image.alt = `Portrait of ${displayName(node.speaker, "the speaker")}`;
    image.onerror = () => {
      if (image.src !== url.href) return;
      image.hidden = true;
      $("portrait-status").hidden = false;
      warnMedia("The speaker's portrait could not be loaded. Re-export the artwork pack to restore it.");
    };
    if (image.src !== url.href) image.src = url.href;
    else if (image.complete && !image.naturalWidth) image.onerror();
  } else {
    image.onerror = null;
    image.removeAttribute("src");
  }
  const changes = relationshipChanges(summary, profile?.variableName);
  $("relationship-panel").hidden = !changes.length;
  $("relationship-strip").replaceChildren(...changes.map(change => {
    const span = make("span", `relationship-meter ${change.icon}`);
    span.dataset.variable = change.variable;
    span.dataset.routeValue = change.value;
    span.title = `${change.label}: ${change.description}${change.meter === "Enemy" ? ". Enemy is an internal variable name; its UI label is unconfirmed." : ""}`;
    span.setAttribute("aria-label", span.title);
    span.append(icon(change.icon), make("span", "meter-value", change.value));
    return span;
  }));
}

function inspectNode(node) {
  state.inspectedNode = node;
  graph.inspect(node.id);
  $("map-inspection").hidden = false;
  $("map-inspection").open = true;
  $("map-inspection-title").textContent = `Map note · ${nodeLabel(node)}`;
  $("map-inspection-text").textContent = nodeText(node);
  const details = nodeEffects(node, state.route);
  addEffects($("map-inspection-effects"), summarizeEffects(details.effects), "", false, true);
  $("map-inspection-requirements").hidden = !details.conditions.length;
  $("map-inspection-requirements").replaceChildren();
  if (details.conditions.length) {
    $("map-inspection-requirements").append(make("h4", "", "Requirements"), make("p", "", requirementsText(details.conditions)), make("p", "muted", "Shown from the game data, not checked against your save."));
    const raw = make("details"); raw.append(make("summary", "", "Original game rule"), make("pre", "", [...new Set(details.conditions)].join("\n")));
    $("map-inspection-requirements").append(raw);
  }
  refreshOpenResearch();
}

function renderResearch() {
  const { quest, dialogue } = state;
  const limitations = [
    "This is a static source exploration tool, not a save-state simulation. Conditions, assignments, and template actions are not executed.",
    "A visited node’s effects are counted once per visit; each selected edge’s effects are counted when taken. Revisited nodes can change totals again.",
    "Exploration stops at this conversation’s exit. External links and subdialogues are context only.",
    ...(quest ? ["Objectives are listed in source authoring order, not a proven gameplay progression. No objective completion is simulated."] : []),
    ...(dialogue?.limitations || []), ...(dialogue?.warnings || []), ...(quest?.warnings || [])
  ];
  if (dialogue?.analysis?.status && dialogue.analysis.status !== "complete") limitations.push(`Full wording-route enumeration: ${dialogue.analysis.message || dialogue.analysis.status} Effect combinations are analyzed separately.`);
  if (dialogue) limitations.push(effectAnalysis(dialogue).message || "Equivalent accumulated-effect states are merged. Structural combinations are not proof of save eligibility.");
  $("limitations").replaceChildren(...[...new Set(limitations)].map(text => make("li", "", text)));
  $("route-details").hidden = !state.route;
  $("outcome-details").hidden = !dialogue;
  $("all-routes-details").hidden = !dialogue;
  $("source-details").hidden = !dialogue;
  $("quest-details").hidden = !quest;
  if (dialogue) { $("json-link").href = resourceURL(state.catalog.dialogues.find(item => item.id === state.dialogueId)); $("json-link").textContent = "Download conversation JSON"; }
  if (quest) $("quest-json-link").href = resourceURL(state.catalog.quests.find(item => item.id === state.questId));
  $("all-routes").replaceChildren(); $("recorded-outcomes").replaceChildren(); $("route-history").replaceChildren();
  $("source-raw").textContent = ""; $("quest-raw").textContent = ""; $("route-effects").textContent = "";
  refreshOpenResearch();
}

function refreshOpenResearch() {
  if ($("route-details").open && state.route) {
    const route = state.route;
    const list = make("ol");
    for (const id of route.state.visited) {
      const node = route.nodes.get(id);
      list.append(make("li", "", `${nodeLabel(node)}${["line", "choice"].includes(node?.type) ? `: ${nodeText(node)}` : ""}`));
    }
    $("route-history").replaceChildren(list);
    $("route-effects").textContent = JSON.stringify({ summary: summarizeEffects(route.state.effects), effects: route.state.effects, conditionsNotEvaluated: [...new Set(route.state.conditions)], visitedNodeIds: route.state.visited, takenEdgeIds: route.state.taken }, null, 2);
  }
  if ($("source-details").open && state.dialogue) $("source-raw").textContent = JSON.stringify({ dialogueId: state.dialogue.dialogueId, provenance: state.dialogue.provenance, node: state.route?.current || null, inspectedMapNode: state.inspectedNode, outgoingEdges: state.route?.outgoing.get(state.route.state.current) || [], analysis: state.dialogue.analysis }, null, 2);
  if ($("quest-details").open && state.quest) $("quest-raw").textContent = JSON.stringify(state.quest, null, 2);
  if ($("all-routes-details").open && state.dialogue) renderRecordedRoutes();
  if ($("outcome-details").open && state.dialogue) renderRecordedOutcomes();
}

function renderRecordedOutcomes() {
  const analysis = effectAnalysis(state.dialogue), outcomes = analysis.outcomes, holder = $("recorded-outcomes");
  holder.replaceChildren();
  holder.append(make("p", "muted", analysis.message || `${analysis.status} analysis. Requirements are not checked against your save.`), make("pre", "", JSON.stringify({ status: analysis.status, stats: analysis.stats, diagnostics: analysis.diagnostics }, null, 2)));
  for (const [index, outcome] of outcomes.slice(0, 50).entries()) {
    const row = make("div", "recorded-route");
    row.append(make("h4", "", `Recorded outcome ${index + 1}`));
    const effects = make("div", "effect-row");
    addEffects(effects, outcome.summary, "No decoded effect changes.", false, true);
    row.append(effects, make("pre", "", JSON.stringify(outcome, null, 2)));
    holder.append(row);
  }
  if (outcomes.length > 50) holder.append(make("p", "muted", "Showing the first 50 distinct outcomes. The downloadable JSON retains the full set."));
}

function renderRecordedRoutes() {
  const paths = state.dialogue.paths || [], holder = $("all-routes");
  holder.replaceChildren();
  $("more-routes").hidden = state.routeRows >= paths.length;
  if (!paths.length) { holder.append(make("p", "", `${state.dialogue.analysis?.message || "No full wording routes were recorded for this graph."} Effect combinations are available in the summary above; the full-route limit does not control their analysis.`)); return; }
  holder.append(make("p", "muted", `Showing ${Math.min(state.routeRows, paths.length)} of ${paths.length} recorded routes. Routes end at this conversation’s exit; gates are unevaluated.`));
  for (const [index, path] of paths.slice(0, state.routeRows).entries()) {
    const row = make("div", "recorded-route");
    row.append(make("h4", "", `Recorded route ${index + 1}`));
    const choices = make("ol");
    for (const id of path.choices || []) choices.append(make("li", "", state.route?.nodes.get(id)?.text || "Localized choice text unavailable"));
    if (choices.childElementCount) row.append(choices);
    else row.append(make("p", "", "No player reply on this route."));
    const effects = make("div", "effect-row");
    addEffects(effects, path.summary || summarizeEffects(path.effects), "No player-facing relationship or recipe change.");
    row.append(effects);
    const raw = make("details");
    raw.append(make("summary", "", "Route scripts, conditions & node IDs"), make("pre", "", JSON.stringify({ conditionsNotEvaluated: path.conditions || [], effects: path.effects || [], nodes: path.nodes || [] }, null, 2)));
    row.append(raw); holder.append(row);
  }
}

function selectCollection(collection, focusTab = false) {
  state.collection = collection; state.page = 0;
  for (const [id, value] of [["quests-tab", "quests"], ["dialogues-tab", "dialogues"]]) {
    $(id).setAttribute("aria-selected", String(collection === value));
    $(id).tabIndex = collection === value ? 0 : -1;
  }
  $("library-results").scrollTop = 0;
  renderLibrary();
  if (focusTab) $(collection === "quests" ? "quests-tab" : "dialogues-tab").focus();
}

function setView(view, focusTab = false) {
  state.view = view;
  $("workspace").dataset.view = view;
  for (const [id, value] of [["conversation-tab", "conversation"], ["map-tab", "map"]]) {
    $(id).setAttribute("aria-selected", String(value === view));
    $(id).tabIndex = value === view ? 0 : -1;
  }
  if (focusTab) $(view === "map" ? "map-tab" : "conversation-tab").focus();
  if (view === "map") requestAnimationFrame(() => graph.focusCurrent());
}

function openLibrary() {
  if (!window.matchMedia("(max-width: 700px)").matches) { $("library-search").focus(); return; }
  document.body.classList.add("library-open");
  $("library").inert = false;
  $("library").removeAttribute("aria-hidden");
  $("drawer-backdrop").hidden = false;
  $("library-toggle").setAttribute("aria-expanded", "true");
  $("main").inert = true;
  $("library").setAttribute("role", "dialog"); $("library").setAttribute("aria-modal", "true");
  $("library-search").focus();
}

function closeLibrary(restoreFocus = true) {
  const wasOpen = document.body.classList.contains("library-open");
  document.body.classList.remove("library-open");
  $("drawer-backdrop").hidden = true;
  $("library-toggle").setAttribute("aria-expanded", "false");
  $("main").inert = false;
  $("library").inert = window.matchMedia("(max-width: 700px)").matches;
  if ($("library").inert) $("library").setAttribute("aria-hidden", "true"); else $("library").removeAttribute("aria-hidden");
  $("library").removeAttribute("role"); $("library").removeAttribute("aria-modal");
  if (wasOpen && restoreFocus) $("library-toggle").focus();
}

$("retry").addEventListener("click", () => state.retry?.());
$("quests-tab").addEventListener("click", () => selectCollection("quests"));
$("dialogues-tab").addEventListener("click", () => selectCollection("dialogues"));
for (const id of ["quests-tab", "dialogues-tab"]) $(id).addEventListener("keydown", event => {
  if (["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) {
    event.preventDefault();
    selectCollection(event.key === "Home" ? "quests" : event.key === "End" ? "dialogues" : state.collection === "quests" ? "dialogues" : "quests", true);
  }
});
$("library-search").addEventListener("input", event => {
  state.search = event.target.value; state.page = 0;
  $("library-results").scrollTop = 0;
  clearTimeout(state.searchTimer); state.searchController?.abort(); state.searchRequest++;
  if (!state.search.trim()) renderLibrary();
  else state.searchTimer = setTimeout(renderLibrary, 180);
});
$("library-prev").addEventListener("click", () => { state.page--; renderLibrary(); $("library-results").scrollTop = 0; });
$("library-next").addEventListener("click", () => { state.page++; renderLibrary(); $("library-results").scrollTop = 0; });
$("dialogue-select").addEventListener("change", event => navigate({ quest: state.questId, dialogue: event.target.value }));
$("back").addEventListener("click", () => { if (state.route?.back()) { renderConversation(true); $("speaker").focus({ preventScroll: true }); } });
$("restart").addEventListener("click", () => { state.route?.restart(); if (state.route) { renderConversation(true); $("speaker").focus({ preventScroll: true }); } });
$("zoom-in").addEventListener("click", () => graph.setZoom(graph.zoom + .1));
$("zoom-out").addEventListener("click", () => graph.setZoom(graph.zoom - .1));
$("fit-map").addEventListener("click", () => graph.fit());
$("focus-map").addEventListener("click", () => { if (graph.zoom < .55) graph.setZoom(.8, false); graph.focusCurrent(); });
$("expand-map").addEventListener("click", () => {
  state.expanded = !state.expanded;
  $("workspace").classList.toggle("is-expanded", state.expanded);
  $("expand-map").setAttribute("aria-pressed", String(state.expanded));
  $("expand-map").setAttribute("aria-label", state.expanded ? "Return to conversation and map" : "Expand conversation map");
  requestAnimationFrame(() => graph.focusCurrent());
});
$("conversation-tab").addEventListener("click", () => setView("conversation"));
$("map-tab").addEventListener("click", () => setView("map"));
for (const id of ["conversation-tab", "map-tab"]) $(id).addEventListener("keydown", event => {
  if (["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) {
    event.preventDefault();
    setView(event.key === "Home" ? "conversation" : event.key === "End" ? "map" : state.view === "map" ? "conversation" : "map", true);
  }
});
for (const id of ["route-details", "source-details", "quest-details", "outcome-details", "all-routes-details"]) $(id).addEventListener("toggle", () => { if ($(id).open) refreshOpenResearch(); });
$("more-routes").addEventListener("click", () => { state.routeRows += 20; renderRecordedRoutes(); });
$("more-combinations").addEventListener("click", () => { state.outcomeRows += 12; renderOutcomeCombinations(); });
$("clear-comparison").addEventListener("click", () => setComparison(null));
$("library-toggle").addEventListener("click", openLibrary);
$("drawer-close").addEventListener("click", () => closeLibrary());
$("drawer-backdrop").addEventListener("click", () => closeLibrary());
document.addEventListener("keydown", event => {
  if (event.key === "Escape") { if (document.body.classList.contains("library-open")) closeLibrary(); else if (state.expanded) $("expand-map").click(); }
  if (event.key === "Tab" && document.body.classList.contains("library-open")) {
    const focusable = [...$("library").querySelectorAll("a[href],button:not(:disabled),input,summary")].filter(element => element.tabIndex >= 0 && element.getClientRects().length);
    const first = focusable[0], last = focusable[focusable.length - 1];
    if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
    else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
  }
});
window.matchMedia("(max-width: 700px)").addEventListener("change", () => closeLibrary(false));
window.matchMedia("(max-width: 980px)").addEventListener("change", event => {
  if (event.matches && state.expanded) { state.expanded = false; $("workspace").classList.remove("is-expanded"); $("expand-map").setAttribute("aria-pressed", "false"); $("expand-map").setAttribute("aria-label", "Expand conversation map"); }
  requestAnimationFrame(() => graph.focusCurrent());
});
window.addEventListener("hashchange", () => { if (state.catalog) { const params = paramsFromHash(); loadRoute(params.quest || params.dialogue ? params : { quest: state.catalog.defaultQuestId, dialogue: state.catalog.defaultDialogueId }, true); } });
closeLibrary(false);
bootstrap();
