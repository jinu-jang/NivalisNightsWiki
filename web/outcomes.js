import { describedEffects, relationshipOf, signed, summarizeEffects } from "./route.js";
import { displayName, variableLabel } from "./labels.js";

export function effectAnalysis(dialogue) {
  if (dialogue.effectAnalysis) return dialogue.effectAnalysis;
  return {
    status: "partial", legacy: true,
    message: "This older pack has only bounded route results. Re-export it for effect-combination analysis.",
    outcomes: (dialogue.outcomes || []).map((summary, index) => ({ id: `legacy-${index}`, summary, conditions: [], witness: null }))
  };
}

export function outcomeItems(analysis) {
  const summaries = analysis.outcomes.map(outcome => outcome.summary), result = [];
  const variables = new Set(summaries.flatMap(summary => Object.keys(summary.deltas || {})));
  for (const variable of variables) {
    const relation = relationshipOf(variable);
    if (!relation && variable !== "GlobalInventory.Lims") continue;
    if (summaries.some(summary => Object.hasOwn(summary.assignments || {}, variable))) continue;
    const values = summaries.map(summary => summary.deltas?.[variable] || 0);
    const low = Math.min(...values), high = Math.max(...values);
    if (low || high) result.push({ ...(relation || { icon: "counter", variable }), kind: "range", low, high, text: `${relation ? `${relation.actor} \u00b7 ${relation.label}` : variableLabel(variable)} ${low === high ? signed(low) : `${signed(low)} to ${signed(high)}`}` });
  }
  const recipes = new Set(summaries.flatMap(summary => summary.recipes || []));
  for (const recipe of recipes) result.unshift({ icon: "recipe", kind: "unlock_recipe", recipe, text: `${displayName(recipe)} recipe` });
  const seen = new Set();
  for (const summary of summaries) {
    for (const item of describedEffects(summary)) {
      if (!["template_action", "set"].includes(item.kind)) continue;
      const key = `${item.kind}:${item.text}`;
      if (seen.has(key)) continue;
      seen.add(key); result.push(item);
    }
  }
  return result;
}

export function reachableEffectItems(dialogue) {
  const nodes = new Map(dialogue.nodes.map(node => [node.id, node]));
  const outgoing = new Map(dialogue.nodes.map(node => [node.id, []]));
  for (const edge of dialogue.edges) outgoing.get(edge.from)?.push(edge);
  const queue = [dialogue.entry], visited = new Set(), found = new Map();
  const record = effects => {
    for (const item of describedEffects(summarizeEffects(effects))) found.set(JSON.stringify([item.kind, item.variable, item.value, item.recipe, item.text]), item);
  };
  for (let index = 0; index < queue.length; index++) {
    const id = queue[index];
    if (visited.has(id)) continue;
    visited.add(id);
    const node = nodes.get(id);
    if (!node) continue;
    record(node.effects || []);
    if (id === dialogue.exit || ["external", "subdialogue"].includes(node.type)) continue;
    for (const edge of outgoing.get(id) || []) { record(edge.effects || []); queue.push(edge.to); }
  }
  return [...found.values()];
}

export function orderedOutcomes(analysis) {
  const benefit = outcome => (outcome.summary.recipes || []).length * 100 + Object.entries(outcome.summary.deltas || {}).reduce((score, [variable, value]) => {
    const relation = relationshipOf(variable);
    return score + (relation && relation.meter !== "Enemy" ? value : 0);
  }, 0);
  return [...analysis.outcomes].sort((a, b) => benefit(b) - benefit(a) || a.id.localeCompare(b.id));
}
