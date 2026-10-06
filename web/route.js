const terminalTypes = new Set(["exit", "external", "subdialogue"]);

export function displayName(value, fallback = "Unnamed") {
  const text = String(value || "").trim();
  if (!text || /^(?:Dlg|DFr|FFr|Obj|Ins|Con|Chr|CFr|Rec|Rcp|Itm|Item)_[A-F\d]+$/i.test(text) || /^\d{10,}$/.test(text)) return fallback;
  return text.replace(/_/g, " ").replace(/([a-z])([A-Z])/g, "$1 $2").replace(/\s+/g, " ").trim();
}

export function relationshipOf(variable) {
  const match = /^(.*)\.(Friend|Enemy|Romance|Business)$/.exec(variable || "");
  return match ? { variable, actor: displayName(match[1], "Relationship"), meter: match[2], label: match[2] === "Friend" ? "Friendly" : match[2] === "Enemy" ? "Enemy (UI unconfirmed)" : match[2], icon: match[2] === "Friend" ? "friendly" : match[2].toLowerCase() } : null;
}

export function summarizeEffects(effects = []) {
  const summary = { deltas: {}, assignments: {}, recipes: [], unparsed: [], actions: [] };
  for (const effect of effects) {
    if (effect.type === "delta" && Number.isFinite(effect.value)) summary.deltas[effect.variable] = (summary.deltas[effect.variable] || 0) + effect.value;
    else if (effect.type === "set") summary.assignments[effect.variable] = effect.value;
    else if (effect.type === "unlock_recipe" && !summary.recipes.includes(effect.recipe)) summary.recipes.push(effect.recipe);
    else if (effect.type === "template_action" && effect.label) summary.actions.push(effect.label);
    else if (effect.type === "unparsed") summary.unparsed.push(effect.script || "Unresolved instruction");
  }
  return summary;
}

export const signed = value => value > 0 ? `+${value}` : String(value);

export function meaningfulEffects(summary, includeZero = false) {
  const result = [];
  for (const [variable, value] of Object.entries(summary.deltas || {})) {
    const relation = relationshipOf(variable);
    if (relation && (value !== 0 || includeZero)) result.push({ ...relation, text: `${relation.actor} · ${relation.label} ${signed(value)}`, value, kind: "delta" });
  }
  for (const [variable, value] of Object.entries(summary.assignments || {})) {
    const relation = relationshipOf(variable);
    if (relation) result.push({ ...relation, text: `${relation.actor} · ${relation.label} set to ${value}`, value, kind: "set" });
  }
  for (const recipe of summary.recipes || []) result.push({ icon: "recipe", text: `${displayName(recipe, "Unnamed")} recipe`, recipe, kind: "unlock_recipe" });
  for (const label of new Set([...(summary.actions || []), ...(summary.instructions || [])])) result.push({ icon: "notebook", text: label, kind: "template_action" });
  return result;
}

export function nodeLabel(node) {
  if (!node) return "Missing node";
  if (node.type === "line") return displayName(node.speaker, "Speaker");
  return ({ choice: "Your reply", action: "Instruction", condition: "Unevaluated gate", entry: "Conversation starts", exit: "Conversation ends", external: "External context", subdialogue: "Linked conversation" })[node.type] || "Conversation node";
}

export function nodeText(node) {
  if (!node) return "This node was not exported.";
  if (node.type === "action") return meaningfulEffects(summarizeEffects(node.effects)).map(item => item.text).join(" · ") || "Extracted instruction. Details in Research notes.";
  if (node.type === "condition") return "Eligibility is not evaluated by this guide.";
  if (node.type === "entry") return "Begin this conversation.";
  if (node.type === "exit") return "End of this conversation. Outgoing links are context only.";
  if (["external", "subdialogue"].includes(node.type)) return displayName(node.text, "A separate conversation or source context.");
  return node.text || "No localized text was exported.";
}

export class DialogueRoute {
  constructor(data) {
    this.data = data;
    this.nodes = new Map((data.nodes || []).map(node => [String(node.id), node]));
    this.outgoing = new Map([...this.nodes.keys()].map(id => [id, []]));
    this.edges = (data.edges || []).map((edge, index) => ({ ...edge, id: String(edge.id ?? `edge-${index}`), from: String(edge.from), to: String(edge.to) }));
    for (const edge of this.edges) if (this.outgoing.has(edge.from)) this.outgoing.get(edge.from).push(edge);
    for (const edges of this.outgoing.values()) edges.sort((a, b) => (a.pinOrder || 0) - (b.pinOrder || 0) || (a.order || 0) - (b.order || 0));
    this.restart();
  }

  restart() {
    this.history = [];
    this.state = { current: null, visited: [], taken: [], effects: [], conditions: [], choices: [], stop: "empty" };
    const entry = this.data.entry || [...this.nodes.values()].find(node => node.type === "entry")?.id;
    if (entry) this.walk(String(entry));
  }

  get current() { return this.nodes.get(this.state.current); }
  get alternatives() { return terminalTypes.has(this.current?.type) ? [] : this.outgoing.get(this.state.current) || []; }
  get canBack() { return this.history.length > 0; }

  choose(edgeId) {
    const edge = this.alternatives.find(item => item.id === edgeId);
    if (!edge) return false;
    this.history.push(this.snapshot());
    this.take(edge);
    this.walk(edge.to, this.nodes.get(edge.to)?.type === "choice");
    return true;
  }

  back() {
    if (!this.canBack) return false;
    this.state = this.history.pop();
    return true;
  }

  snapshot() {
    return { ...this.state, visited: [...this.state.visited], taken: [...this.state.taken], effects: [...this.state.effects], conditions: [...this.state.conditions], choices: [...this.state.choices] };
  }

  take(edge) {
    this.state.taken.push(edge.id);
    this.state.effects.push(...(edge.effects || []));
    this.state.conditions.push(...(edge.conditions || []));
  }

  walk(start, approvedChoice = false) {
    const seen = new Set();
    let id = start;
    for (let steps = 0; steps <= this.nodes.size; steps++) {
      const node = this.nodes.get(id);
      if (!node) { this.state.current = id; this.state.stop = "missing"; return; }
      seen.add(id);
      this.state.current = id;
      this.state.visited.push(id);
      this.state.effects.push(...(node.effects || []));
      this.state.conditions.push(...(node.conditions || []));
      if (node.type === "choice") this.state.choices.push(id);
      if (terminalTypes.has(node.type)) { this.state.stop = node.type; return; }
      if (node.type === "line") { this.state.stop = "line"; return; }
      const edges = this.outgoing.get(id) || [];
      if (!edges.length) { this.state.stop = "dead_end"; return; }
      if (node.type === "choice" && !approvedChoice) { this.state.stop = "reply"; return; }
      approvedChoice = false;
      if (node.type === "condition" || (node.conditions || []).length) { this.state.stop = "gate"; return; }
      if (edges.length !== 1) { this.state.stop = "branch"; return; }
      const next = edges[0];
      if ((next.conditions || []).length || (this.nodes.get(next.to)?.conditions || []).length || this.nodes.get(next.to)?.type === "condition") { this.state.stop = "gate"; return; }
      if (seen.has(next.to)) { this.state.stop = "cycle"; return; }
      this.take(next);
      id = next.to;
    }
    this.state.stop = "cycle";
  }

  preview(edge) {
    const effects = [...(edge.effects || [])], conditions = [...(edge.conditions || [])], seen = new Set();
    let id = edge.to, stop = "";
    while (!seen.has(id)) {
      seen.add(id);
      const node = this.nodes.get(id);
      if (!node) { stop = "missing"; break; }
      effects.push(...(node.effects || []));
      conditions.push(...(node.conditions || []));
      if (terminalTypes.has(node.type) || node.type === "line") { stop = node.type; break; }
      const edges = this.outgoing.get(id) || [];
      if (node.type === "condition" || (node.conditions || []).length) { stop = "gate"; break; }
      if (edges.length !== 1) { stop = edges.length ? "branch" : "dead_end"; break; }
      const next = edges[0];
      if ((next.conditions || []).length || (this.nodes.get(next.to)?.conditions || []).length || this.nodes.get(next.to)?.type === "condition") {
        conditions.push(...(next.conditions || []), ...(this.nodes.get(next.to)?.conditions || []));
        stop = "gate"; break;
      }
      if (seen.has(next.to)) { stop = "cycle"; break; }
      effects.push(...(next.effects || []));
      id = next.to;
    }
    return { summary: summarizeEffects(effects), conditions: [...new Set(conditions)], stop: stop || "cycle" };
  }
}
