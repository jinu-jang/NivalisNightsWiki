import { DialogueRoute, displayName, relationshipOf, summarizeEffects, meaningfulEffects, nodeLabel, nodeText, signed } from "./route.js";
import { ConversationGraph } from "./graph.js";

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
  controller: null, retry: null, view: "conversation", expanded: false, inspectedNode: null
};
const questCache = new Map(), dialogueCache = new Map();
class GuideDataError extends Error {
  constructor(kind, message) { super(message); this.kind = kind; }
}
const graph = new ConversationGraph($("graph"), $("graph-viewport"), {
  onInspect: inspectNode,
  onZoom: percent => { const label = percent < 1 ? "<1%" : `${percent}%`; $("zoom-level").value = label; $("zoom-level").textContent = label; }
});

function icon(name) {
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("class", "icon"); svg.setAttribute("aria-hidden", "true");
  const use = document.createElementNS("http://www.w3.org/2000/svg", "use");
  use.setAttribute("href", `assets/icons.svg#${name}`);
  svg.append(use);
  return svg;
}

function effectElement(item) {
  const span = make("span", `effect ${item.icon}`);
  span.dataset.effectKind = item.kind || "outcome";
  if (item.variable) span.dataset.variable = item.variable;
  if (item.value !== undefined) span.dataset.value = String(item.value);
  if (item.recipe) span.dataset.recipe = item.recipe;
  span.append(icon(item.icon), make("span", "", item.text));
  if (item.icon === "enemy") span.title = "Internal Enemy variable; exact in-game UI meaning is unconfirmed.";
  else if (item.icon === "romance") span.title = "Romance is an internal relationship variable.";
  return span;
}

function addEffects(holder, summary, emptyText = "", includeZero = false) {
  holder.replaceChildren(...meaningfulEffects(summary, includeZero).map(effectElement));
  if (!holder.childElementCount && emptyText) holder.append(make("p", "route-empty", emptyText));
}

function resourceURL(metadata) {
  if (!metadata?.file) throw new Error("This record has no exported JSON file.");
  const url = new URL(metadata.file, catalogURL);
  const directory = new URL(".", catalogURL).pathname;
  if (url.origin !== catalogURL.origin || !url.pathname.startsWith(directory) || !["http:", "https:", "file:"].includes(url.protocol)) throw new Error("The catalog points outside this guide’s local data collection.");
  return url;
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
  document.title = "Nivalis Fieldnotes — Bring your own fieldnotes";
  showStatus("The city is waiting.", "Your guide is ready. This edition has no quest or conversation data loaded yet.", bootstrap, { kind: "empty", packGuide: true });
}

function paramsFromHash() {
  const params = new URLSearchParams(location.hash.slice(1));
  return { quest: params.get("quest"), dialogue: params.get("dialogue"), objective: params.get("objective") };
}

function setHash(params, replace = false) {
  const query = new URLSearchParams();
  for (const key of ["quest", "dialogue", "objective"]) if (params[key]) query.set(key, params[key]);
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
  showStatus("Opening your fieldnotes…", "Loading the quest and conversation library.");
  $("library-search").disabled = true;
  try {
    const catalog = await readJSON(catalogURL);
    if (!catalog || catalog.schemaVersion !== 2 || !Array.isArray(catalog.quests) || !Array.isArray(catalog.dialogues) || [...catalog.quests, ...catalog.dialogues].some(item => !item || typeof item.id !== "string" || typeof item.file !== "string")) throw new GuideDataError("schema", "The library requires a schema version 2 catalog containing valid quest and conversation records.");
    state.catalog = catalog;
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
    document.title = "Nivalis Fieldnotes — Library unavailable";
    showStatus(malformed ? "This data pack needs attention." : "The library couldn’t be opened.", malformed ? "The library file is unreadable or uses a different format. Re-export or replace the pack, then reload." : "The data pack is missing or couldn’t be reached. Add a local pack or an approved published pack, then reload.", bootstrap, { kind: "error", packGuide: true, detail: error instanceof GuideDataError ? error.message : "The catalog could not be read. Check that its records follow the version 2 schema." });
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
  showStatus("Turning to your next fieldnote…", "Loading the selected quest and conversation.");
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
    if (controller.signal.aborted || request !== state.request) return;
    state.quest = quest; state.questId = questMetadata?.id || null;
    state.objective = objective; state.dialogue = dialogue; state.dialogueId = dialogueMetadata?.id || null;
    state.route = dialogue?.nodes.length ? new DialogueRoute(dialogue) : null;
    state.routeRows = 20;
    state.inspectedNode = null;
    state.expanded = false;
    $("workspace").classList.remove("is-expanded");
    $("expand-map").setAttribute("aria-pressed", "false");
    $("expand-map").setAttribute("aria-label", "Expand conversation map");
    $("map-inspection").hidden = true;
    setView("conversation", false);
    renderGuide();
    renderLibrary();
    setHash({ quest: state.questId, dialogue: state.dialogueId, objective: state.objective?.id }, replace);
    $("page-status").hidden = true;
    $("guide-content").hidden = false;
    $("main").setAttribute("aria-busy", "false");
    window.scrollTo({ top: 0, behavior: "auto" });
    if (request > 1) { $("guide-title").tabIndex = -1; $("guide-title").focus({ preventScroll: true }); }
    if (state.route) graph.update(state.route.state);
  } catch (error) {
    if (error.name === "AbortError" || request !== state.request) return;
    showStatus("This fieldnote couldn’t be opened", error instanceof TypeError || error instanceof SyntaxError ? "This fieldnote is incomplete or unreadable. Re-export or replace the data pack, or choose another record." : error.message, () => loadRoute(params), { detail: error instanceof GuideDataError ? error.message : "" });
  }
}

function renderLibrary() {
  if (!state.catalog) return;
  const questMode = state.collection === "quests";
  const collection = state.catalog[state.collection];
  const tokens = state.search.toLocaleLowerCase().trim().split(/\s+/).filter(Boolean);
  const filtered = collection.filter(item => {
    const haystack = [item.title, item.description, item.owner, ...(item.speakers || [])].filter(Boolean).join(" ").toLocaleLowerCase();
    return tokens.every(token => haystack.includes(token));
  });
  const pages = Math.max(1, Math.ceil(filtered.length / pageSize));
  state.page = Math.max(0, Math.min(state.page, pages - 1));
  const start = state.page * pageSize;
  const holder = $("library-results");
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
    if (item.id === (questMode ? state.questId : state.dialogueId)) button.setAttribute("aria-current", "page");
    button.addEventListener("click", () => navigate(questMode ? { quest: item.id } : { dialogue: item.id }));
    holder.append(button);
  }
  if (!filtered.length) holder.append(make("p", "library-empty", state.search ? "No matching fieldnotes. Try a different title or person." : !collection.length ? "Your fieldnotes will appear here when a data pack is added." : "Nothing in this collection has been exported."));
  $("library-count").textContent = filtered.length ? `${start + 1}–${Math.min(start + pageSize, filtered.length)} of ${filtered.length} ${questMode ? "quests" : "conversations"}` : state.search ? "No results" : "No fieldnotes loaded";
  $("library-pagination").hidden = pages <= 1;
  $("library-page").textContent = `${state.page + 1} / ${pages}`;
  $("library-prev").disabled = state.page === 0;
  $("library-next").disabled = state.page === pages - 1;
}

function renderGuide() {
  const { quest, dialogue, objective } = state;
  const title = displayName(quest?.title || dialogue?.title, quest ? "Untitled quest" : "Untitled conversation");
  $("guide-title").textContent = title;
  document.title = `${title} — Nivalis Fieldnotes`;
  $("quest-description").textContent = quest?.description || "";
  $("quest-description").hidden = !$("quest-description").textContent;
  $("quest-owner").textContent = quest?.owner ? `With ${displayName(quest.owner, "an unknown quest giver")}` : "";
  $("quest-owner").hidden = !$("quest-owner").textContent;
  $("quest-overview").hidden = !quest;
  $("quest-overview").open = false;
  $("overview-count").textContent = `${quest?.objectives.length || 0} objectives`;
  const breadcrumbs = $("breadcrumbs");
  breadcrumbs.replaceChildren(make("span", "", "Fieldnotes"), make("span", "", "/"));
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
  $("dialogue-title").textContent = dialogue ? displayName(dialogue.title, "Conversation") : "Objective fieldnote";
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
    $("task-message").textContent = dialogue ? "This conversation record is empty. Its source and extraction warnings are available in Research notes." : objective ? `${objective.description || (objective.targetName ? `Target: ${displayName(objective.targetName)}` : "This objective is not linked to a dialogue.")}\n\nThis is a quest task, not a conversation. No missing dialogue link has been invented; progress and completion are not simulated.` : "This quest has no exported dialogue link for the selected fieldnote. Browse its objectives or choose another quest.";
  }
  renderResearch();
}

function renderPossibleChanges() {
  const holder = $("possible-changes");
  holder.replaceChildren();
  const outcomes = state.dialogue.outcomes || [];
  const effects = [];
  if (outcomes.length) {
    const variables = new Set(outcomes.flatMap(summary => Object.keys(summary.deltas || {})));
    for (const variable of variables) {
      const relation = relationshipOf(variable);
      if (!relation) continue;
      const values = outcomes.map(summary => summary.deltas?.[variable] || 0);
      const low = Math.min(...values), high = Math.max(...values);
      if (low || high) effects.push({ ...relation, text: `${relation.actor} · ${relation.label} ${low === high ? signed(low) : `${signed(low)} to ${signed(high)}`}` });
    }
    for (const recipe of new Set(outcomes.flatMap(summary => summary.recipes || []))) effects.push({ icon: "recipe", text: `${displayName(recipe)} recipe` });
    for (const label of new Set(outcomes.flatMap(summary => summary.actions || []))) effects.push({ icon: "notebook", text: label });
  } else {
    const reachable = new Set(), queue = [state.dialogue.entry];
    const extracted = [];
    for (let index = 0; index < queue.length; index++) {
      const id = queue[index];
      if (reachable.has(id)) continue;
      reachable.add(id);
      const node = state.route.nodes.get(id);
      if (!node) continue;
      extracted.push(...(node.effects || []));
      if (["exit", "external", "subdialogue"].includes(node.type)) continue;
      for (const edge of state.route.outgoing.get(id) || []) { extracted.push(...(edge.effects || [])); queue.push(edge.to); }
    }
    const seen = new Set();
    for (const item of extracted) {
      const relation = relationshipOf(item.variable);
      const key = relation ? item.variable : item.type === "unlock_recipe" ? `recipe:${item.recipe}` : item.type === "template_action" && item.label ? `action:${item.label}` : "";
      if (!key || seen.has(key)) continue;
      seen.add(key);
      effects.push(relation ? { ...relation, text: `${relation.actor} · ${relation.label}` } : item.type === "unlock_recipe" ? { icon: "recipe", text: `${displayName(item.recipe)} recipe` } : { icon: "notebook", text: item.label });
    }
  }
  if (!effects.length) return;
  holder.append(make("p", "", outcomes.length ? "Possible changes on recorded complete routes" : "Changes found in this conversation · complete routes unavailable"));
  effects.sort((a, b) => Number(b.icon === "recipe") - Number(a.icon === "recipe"));
  holder.append(...effects.slice(0, 6).map(effectElement));
  if (effects.length > 6) holder.append(make("span", "", "More in Research notes"));
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
  if (target?.type === "condition") return `Explore branch ${index + 1} · gate not evaluated`;
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
  else if (stop === "gate") note = "A gate lies ahead. Choose a branch to inspect its hypothetical route; this guide cannot determine eligibility.";
  else if (stop === "dead_end" || !route.alternatives.length) note = "No outgoing continuation was exported for this node. This is not a confirmed complete route.";
  $("conversation-note").textContent = note;
  const routeSummary = summarizeEffects(route.state.effects);
  $("route-snapshot").hidden = !meaningfulEffects(routeSummary, true).length;
  addEffects($("route-stats"), routeSummary, "", true);
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
    addEffects(outcomes, preview.summary);
    copy.append(outcomes);
    if (preview.conditions.length || preview.stop === "gate" || stop === "gate" || node?.type === "condition") copy.append(make("span", "gate-hint", "Conditions not evaluated · eligibility unknown"));
    else if (["branch", "cycle"].includes(preview.stop)) copy.append(make("span", "gate-hint", "More branches ahead · preview stops before a decision"));
    const arrow = icon("arrow"); arrow.classList.add("choice-arrow");
    button.append(copy, arrow);
    button.addEventListener("click", () => { if (route.choose(edge.id)) { renderConversation(true); $("speaker").focus({ preventScroll: true }); } });
    $("choices").append(button);
  }
  if (stop === "exit") $("choices").append(make("p", "branch-note", "Step back to try a different reply, or start again to compare routes."));
  const warning = stop === "cycle" ? "An automatic loop has been paused. Continue deliberately or go back. Revisiting a node will count its effects again." : route.state.conditions.length || stop === "gate" ? "Conditional route: all gate conditions remain unevaluated. These are hypothetical route changes, not verified eligibility." : "";
  $("route-warning").textContent = warning;
  $("route-warning").hidden = !warning;
  $("back").disabled = !route.canBack;
  graph.update(route.state);
  refreshOpenResearch();
  if (announce) $("route-announcement").textContent = `${$("speaker").textContent}. ${node?.type === "line" ? node.text : note} ${alternatives.length ? `${alternatives.length} continuation options.` : ""}`;
}

function inspectNode(node) {
  state.inspectedNode = node;
  $("map-inspection").hidden = false;
  $("map-inspection").open = true;
  $("map-inspection-title").textContent = `Map note · ${nodeLabel(node)}`;
  $("map-inspection-text").textContent = nodeText(node) + ((node.conditions || []).length ? "\nThis node has conditions. Eligibility has not been evaluated." : "");
  addEffects($("map-inspection-effects"), summarizeEffects(node.effects));
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
  if (dialogue?.analysis?.status && dialogue.analysis.status !== "complete") limitations.push(dialogue.analysis.message || `Complete-route enumeration status: ${dialogue.analysis.status}.`);
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
  const outcomes = state.dialogue.outcomes || [], holder = $("recorded-outcomes");
  holder.replaceChildren();
  if (!outcomes.length) { holder.append(make("p", "", state.dialogue.analysis?.message || "No complete outcomes were recorded. Cycles, extraction boundaries, or an enumeration limit may prevent complete-route analysis.")); return; }
  holder.append(make("p", "muted", `${outcomes.length} recorded distinct outcomes. Gates are not evaluated; these are not guaranteed reachable in your save.`));
  for (const [index, summary] of outcomes.slice(0, 50).entries()) {
    const row = make("div", "recorded-route");
    row.append(make("h4", "", `Recorded outcome ${index + 1}`));
    const effects = make("div", "effect-row");
    addEffects(effects, summary, "No player-facing relationship or recipe change.");
    row.append(effects, make("pre", "", JSON.stringify(summary, null, 2)));
    holder.append(row);
  }
  if (outcomes.length > 50) holder.append(make("p", "muted", "Showing the first 50 distinct outcomes. The downloadable JSON retains the full set."));
}

function renderRecordedRoutes() {
  const paths = state.dialogue.paths || [], holder = $("all-routes");
  holder.replaceChildren();
  $("more-routes").hidden = state.routeRows >= paths.length;
  if (!paths.length) { holder.append(make("p", "", state.dialogue.analysis?.message || "No complete routes were recorded for this graph. You can still explore its available nodes above.")); return; }
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
$("library-search").addEventListener("input", event => { state.search = event.target.value; state.page = 0; $("library-results").scrollTop = 0; renderLibrary(); });
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
