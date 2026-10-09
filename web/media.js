import { signed } from "./route.js";

const meters = [
  { meter: "Friend", icon: "friendly", label: "Friendly" },
  { meter: "Enemy", icon: "enemy", label: "Enemy" },
  { meter: "Romance", icon: "romance", label: "Romance" },
  { meter: "Business", icon: "business", label: "Business" }
];

class MediaDataError extends Error {
  constructor(message) { super(message); this.kind = "schema"; }
}

export function dataURL(file, baseURL, catalogURL) {
  if (typeof file !== "string" || !file) throw new Error("This record has no exported file.");
  const url = new URL(file, baseURL);
  const directory = new URL(".", catalogURL).pathname;
  if (url.origin !== catalogURL.origin || !url.pathname.startsWith(directory) || !["http:", "https:", "file:"].includes(url.protocol)) throw new Error("The record points outside this wiki's local data collection.");
  return url;
}

export function validateMedia(value) {
  const record = item => item && typeof item === "object" && !Array.isArray(item);
  if (!record(value) || value.schemaVersion !== 2 || !record(value.characters) || !record(value.relationships)) throw new MediaDataError("The artwork manifest must use schema version 2 and contain character and relationship records.");
  for (const { icon } of meters) {
    if (typeof value.relationships[icon]?.file !== "string") throw new MediaDataError(`The artwork manifest has no ${icon} symbol.`);
  }
  for (const [id, character] of Object.entries(value.characters)) {
    if (!record(character) || (character.portrait != null && typeof character.portrait.file !== "string")) throw new MediaDataError(`The artwork manifest contains an invalid character: ${id}.`);
  }
  const bubble = value.ui?.dialogueBubble;
  if (bubble && (typeof bubble.file !== "string" || bubble.imageType !== 1 || !record(bubble.border) || ["top", "right", "bottom", "left"].some(side => !Number.isFinite(bubble.border[side]) || bubble.border[side] < 0))) throw new MediaDataError("The dialogue frame has invalid nine-slice artwork metadata.");
  if (value.ui?.questBadge && typeof value.ui.questBadge.file !== "string") throw new MediaDataError("The quest emblem has no artwork file.");
  return value;
}

export function speakerProfile(media, node) {
  return node?.type === "line" && node.speakerId ? media?.characters[node.speakerId] || null : null;
}

export function relationshipChanges(summary, variableName) {
  if (typeof variableName !== "string" || !variableName) return [];
  return meters.map(definition => {
    const variable = `${variableName}.${definition.meter}`;
    const delta = summary.deltas?.[variable] || 0;
    const assigned = Object.hasOwn(summary.assignments || {}, variable);
    const assignment = summary.assignments?.[variable];
    const value = assigned ? `=${assignment}${delta ? ` / ${signed(delta)}` : ""}` : signed(delta);
    const description = assigned ? `set to ${assignment}${delta ? `; also ${signed(delta)} in recorded deltas` : ""}` : `${signed(delta)} on this route`;
    return { ...definition, variable, value, description };
  });
}
