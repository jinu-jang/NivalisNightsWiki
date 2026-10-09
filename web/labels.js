export function displayName(value, fallback = "Unnamed") {
  const text = String(value || "").trim();
  if (!text || /^(?:Dlg|DFr|FFr|Obj|Ins|Con|Chr|CFr|Rec|Rcp|Itm|Item)_[A-F\d]+$/i.test(text) || /^\d{10,}$/.test(text)) return fallback;
  return text.replace(/_/g, " ").replace(/([a-z])([A-Z])/g, "$1 $2").replace(/\s+/g, " ").trim();
}

export const signed = value => value > 0 ? `+${value}` : String(value);

const namedVariables = {
  "GlobalInventory.Lims": "Lims",
  "GlobalMorality.Corp": "Corp morality",
  "GameDay.Day": "Game day",
  "GameState.NightMode": "Night mode",
  "GameState.Debt": "Current debt"
};

export function variableLabel(variable) {
  if (namedVariables[variable]) return namedVariables[variable];
  const parts = String(variable).split(".");
  const field = displayName(parts.pop(), "State");
  const actor = displayName(parts.join(" "), "Game");
  return `${actor} \u00b7 ${field}`;
}

export function assignmentLabel(variable, value) {
  const met = /^(.*)\.Met$/.exec(variable);
  if (met && typeof value === "boolean") return value ? `Record meeting ${displayName(met[1])}` : `Clear meeting record for ${displayName(met[1])}`;
  return typeof value === "boolean" ? `${value ? "Enable" : "Disable"} ${variableLabel(variable)}` : `Set ${variableLabel(variable)} to ${value}`;
}

export function internalEffects(summary) {
  const result = [];
  const isRelationship = variable => /\.(Friend|Enemy|Romance|Business)$/.test(variable);
  for (const [variable, value] of Object.entries(summary.deltas || {})) {
    if (!isRelationship(variable) && value) result.push({ icon: "counter", kind: "delta", variable, value, text: `${variableLabel(variable)} ${signed(value)}`, internal: variable !== "GlobalInventory.Lims" });
  }
  for (const [variable, value] of Object.entries(summary.assignments || {})) {
    if (!isRelationship(variable)) result.push({ icon: "flag", kind: "set", variable, value, text: assignmentLabel(variable, value), internal: true });
  }
  for (const script of new Set(summary.unparsed || [])) result.push({ icon: "condition", kind: "unparsed", text: `Uninterpreted script: ${script}`, script, internal: true });
  return result;
}

function splitRule(text, operator) {
  let depth = 0, quoted = null, start = 0;
  const parts = [];
  for (let i = 0; i < text.length; i++) {
    const char = text[i];
    if (quoted) { if (char === quoted && text[i - 1] !== "\\") quoted = null; continue; }
    if (char === "'" || char === '"') { quoted = char; continue; }
    if (char === "(") depth++;
    else if (char === ")") depth--;
    else if (depth === 0 && text.slice(i, i + operator.length) === operator) {
      parts.push(text.slice(start, i).trim()); start = i + operator.length; i += operator.length - 1;
    }
  }
  if (!parts.length) return null;
  parts.push(text.slice(start).trim());
  return parts;
}

function flagRequirement(variable, enabled) {
  const met = /^(.*)\.Met$/.exec(variable);
  if (met) return `${enabled ? "Have met" : "Have not met"} ${displayName(met[1])}`;
  const hired = /^(.*)\.IsHired$/.exec(variable);
  if (hired) return `${displayName(hired[1])} ${enabled ? "is hired" : "is not hired"}`;
  if (variable === "GameState.NightMode") return enabled ? "Night mode is active" : "Night mode is not active";
  return `${variableLabel(variable)} is ${enabled ? "enabled" : "disabled"}`;
}

export function conditionText(expression, depth = 0) {
  const text = String(expression || "").trim();
  if (!text) return "No requirement was specified";
  if (depth > 20) return `Game rule: ${text}`;
  for (const [operator, join] of [["||", " OR "], ["&&", " AND "]]) {
    const parts = splitRule(text, operator);
    if (parts) return parts.map(part => {
      const label = conditionText(part, depth + 1);
      return / AND | OR /.test(label) ? `(${label})` : label;
    }).join(join);
  }
  if (text.startsWith("(") && text.endsWith(")")) {
    let nesting = 0, encloses = true;
    for (let i = 0; i < text.length - 1; i++) {
      if (text[i] === "(") nesting++;
      else if (text[i] === ")" && --nesting === 0) { encloses = false; break; }
    }
    if (encloses) return conditionText(text.slice(1, -1), depth + 1);
  }
  const flag = /^(!\s*)?([A-Za-z_]\w*(?:\.[A-Za-z_]\w*)+)$/.exec(text);
  if (flag) return flagRequirement(flag[2], !flag[1]);
  const comparison = /^([A-Za-z_]\w*(?:\.[A-Za-z_]\w*)+)\s*(==|!=|>=|<=|>|<)\s*(true|false|-?\d+(?:\.\d+)?)$/.exec(text);
  if (comparison) {
    const [, variable, operator, literal] = comparison;
    if (["true", "false"].includes(literal) && ["==", "!="].includes(operator)) return flagRequirement(variable, (literal === "true") === (operator === "=="));
    const comparisonLabel = { "==": "equals", "!=": "does not equal", ">=": "is at least", "<=": "is at most", ">": "is greater than", "<": "is less than" }[operator];
    if (variable === "GlobalInventory.Lims") return { ">=": `Have at least ${literal} Lims`, ">": `Have more than ${literal} Lims`, "<": `Have fewer than ${literal} Lims`, "<=": `Have at most ${literal} Lims`, "==": `Have exactly ${literal} Lims`, "!=": `Lims total is not ${literal}` }[operator];
    return `${variableLabel(variable)} ${comparisonLabel} ${literal}`;
  }
  if (text === "true") return "Always true";
  if (text === "false" || text === "true == false") return "Always false in the authored rule";
  return `Game rule: ${text}`;
}

export function requirementsText(conditions = []) {
  return [...new Set(conditions.filter(Boolean))].map(expression => conditionText(expression)).join("; ");
}
