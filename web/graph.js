import { nodeLabel, nodeText } from "./route.js";

const NS = "http://www.w3.org/2000/svg";
const CARD_WIDTH = 210, CARD_HEIGHT = 104, COLUMN = 238, ROW = 154, MARGIN = 32;
const svgElement = (tag, attributes = {}) => {
  const element = document.createElementNS(NS, tag);
  for (const [key, value] of Object.entries(attributes)) element.setAttribute(key, value);
  return element;
};

// Condense strongly connected components before ranking; loops must remain drawable.
export function layoutGraph(nodes, edges) {
  const ids = nodes.map(node => String(node.id)), orderOf = new Map(nodes.map((node, index) => [String(node.id), index]));
  const outgoing = new Map(ids.map(id => [id, []])), incoming = new Map(ids.map(id => [id, []]));
  for (const edge of edges) {
    if (!outgoing.has(String(edge.from)) || !incoming.has(String(edge.to))) continue;
    outgoing.get(String(edge.from)).push(String(edge.to));
    incoming.get(String(edge.to)).push(String(edge.from));
  }
  const seen = new Set(), finish = [];
  for (const start of ids) {
    if (seen.has(start)) continue;
    const stack = [[start, 0]];
    seen.add(start);
    while (stack.length) {
      const frame = stack[stack.length - 1], children = outgoing.get(frame[0]);
      if (frame[1] < children.length) {
        const child = children[frame[1]++];
        if (!seen.has(child)) { seen.add(child); stack.push([child, 0]); }
      } else { finish.push(frame[0]); stack.pop(); }
    }
  }
  const componentOf = new Map(), components = [];
  for (const start of finish.reverse()) {
    if (componentOf.has(start)) continue;
    const index = components.length, component = [], stack = [start];
    componentOf.set(start, index);
    while (stack.length) {
      const id = stack.pop();
      component.push(id);
      for (const previous of incoming.get(id)) if (!componentOf.has(previous)) { componentOf.set(previous, index); stack.push(previous); }
    }
    components.push(component);
  }
  const componentEdges = components.map(() => new Set()), degree = components.map(() => 0), ranks = components.map(() => 0);
  let hasCycles = components.some(component => component.length > 1);
  for (const edge of edges) {
    const from = componentOf.get(String(edge.from)), to = componentOf.get(String(edge.to));
    if (from === undefined || to === undefined) continue;
    if (from === to) { if (edge.from === edge.to) hasCycles = true; continue; }
    if (!componentEdges[from].has(to)) { componentEdges[from].add(to); degree[to]++; }
  }
  const queue = degree.flatMap((value, index) => value === 0 ? [index] : []);
  for (let i = 0; i < queue.length; i++) for (const next of componentEdges[queue[i]]) {
    ranks[next] = Math.max(ranks[next], ranks[queue[i]] + 1);
    if (--degree[next] === 0) queue.push(next);
  }
  const layers = new Map(), byId = new Map(nodes.map(node => [String(node.id), node]));
  for (const id of ids) {
    const rank = ranks[componentOf.get(id)];
    if (!layers.has(rank)) layers.set(rank, []);
    layers.get(rank).push(id);
  }
  const ordered = [...layers.entries()].sort(([a], [b]) => a - b), slots = new Map();
  for (const [, layer] of ordered) {
    layer.sort((a, b) => (byId.get(a).position?.y || 0) - (byId.get(b).position?.y || 0) || orderOf.get(a) - orderOf.get(b));
    layer.forEach((id, index) => slots.set(id, index));
  }
  for (let pass = 0; pass < 3; pass++) {
    for (const [, layer] of ordered) {
      const center = id => {
        const parents = incoming.get(id).filter(parent => componentOf.get(parent) !== componentOf.get(id));
        return parents.length ? parents.reduce((sum, parent) => sum + slots.get(parent), 0) / parents.length : slots.get(id);
      };
      layer.sort((a, b) => center(a) - center(b));
      layer.forEach((id, index) => slots.set(id, index));
    }
  }
  const widest = Math.max(1, ...ordered.map(([, layer]) => layer.length));
  const positions = new Map();
  for (const [rank, layer] of ordered) layer.forEach((id, index) => positions.set(id, {
    x: MARGIN + (widest - layer.length) * COLUMN / 2 + index * COLUMN,
    y: MARGIN + rank * ROW
  }));
  return { positions, width: widest * COLUMN + MARGIN * 2, height: Math.max(1, ordered.length) * ROW + MARGIN * 2, hasCycles };
}

function textLines(text, maxLength = 29, maxLines = 3) {
  const words = String(text).replace(/\s+/g, " ").trim().split(" ");
  const lines = [];
  let line = "";
  for (const word of words) {
    if (line && `${line} ${word}`.length > maxLength) { lines.push(line); line = word; }
    else line += (line ? " " : "") + word;
  }
  if (line) lines.push(line);
  const result = lines.slice(0, maxLines).map(part => part.length > maxLength + 3 ? `${part.slice(0, maxLength)}…` : part);
  if (lines.length > maxLines) result[maxLines - 1] = `${result[maxLines - 1].slice(0, maxLength - 1)}…`;
  return result;
}

export class ConversationGraph {
  constructor(svg, viewport, { onInspect, onZoom } = {}) {
    this.svg = svg;
    this.viewport = viewport;
    this.onInspect = onInspect;
    this.onZoom = onZoom;
    this.zoom = .9;
    this.nodes = new Map();
    this.edgeElements = new Map();
    this.current = null;
    this.focusFrame = null;
  }

  load(data) {
    cancelAnimationFrame(this.focusFrame);
    this.svg.replaceChildren();
    this.nodes.clear(); this.edgeElements.clear();
    this.current = null;
    this.layout = layoutGraph(data.nodes || [], data.edges || []);
    const defs = svgElement("defs");
    for (const [id, color] of [["map-arrow", "#9ca88d"], ["map-arrow-visited", "#365b48"]]) {
      const marker = svgElement("marker", { id, viewBox: "0 0 10 10", refX: "9", refY: "5", markerWidth: "6", markerHeight: "6", orient: "auto-start-reverse" });
      marker.append(svgElement("path", { d: "M 0 0 L 10 5 L 0 10 z", fill: color }));
      defs.append(marker);
    }
    this.svg.append(defs);
    const connections = svgElement("g", { "aria-hidden": "true" });
    for (const [index, edge] of (data.edges || []).entries()) {
      const from = this.layout.positions.get(String(edge.from)), to = this.layout.positions.get(String(edge.to));
      if (!from || !to) continue;
      let pathData;
      if (to.y > from.y) {
        const x = from.x + CARD_WIDTH / 2, y = from.y + CARD_HEIGHT, tx = to.x + CARD_WIDTH / 2, mid = (y + to.y) / 2;
        pathData = `M ${x} ${y} C ${x} ${mid} ${tx} ${mid} ${tx} ${to.y}`;
      } else {
        const x = from.x + CARD_WIDTH, y = from.y + CARD_HEIGHT * .62, tx = to.x + CARD_WIDTH, ty = to.y + CARD_HEIGHT * .38;
        const lane = Math.max(x, tx) + 15 + (index % 3) * 4;
        pathData = `M ${x} ${y} C ${lane} ${y + 55} ${lane} ${ty - 55} ${tx} ${ty}`;
      }
      const path = svgElement("path", { d: pathData, class: `graph-edge${(edge.conditions || []).length ? " gated" : ""}`, "marker-end": "url(#map-arrow)", "data-edge-id": String(edge.id ?? `edge-${index}`), "data-from": edge.from, "data-to": edge.to });
      connections.append(path);
      this.edgeElements.set(String(edge.id ?? `edge-${index}`), path);
    }
    this.svg.append(connections);
    this.orderedIds = [...this.layout.positions.keys()].sort((a, b) => {
      const pa = this.layout.positions.get(a), pb = this.layout.positions.get(b);
      return pa.y - pb.y || pa.x - pb.x;
    });
    for (const node of data.nodes || []) {
      const id = String(node.id), position = this.layout.positions.get(id);
      const group = svgElement("g", { transform: `translate(${position.x},${position.y})`, class: `graph-node ${node.type || ""}`, role: "button", tabindex: "-1", "data-node-id": id, "data-node-type": node.type || "", "aria-label": `Inspect ${nodeLabel(node)}: ${nodeText(node)}` });
      group.append(svgElement("rect", { x: 0, y: 0, width: CARD_WIDTH, height: CARD_HEIGHT, rx: 3 }));
      const title = svgElement("title");
      title.textContent = `${nodeLabel(node)}\n${nodeText(node)}`;
      const label = svgElement("text", { x: 13, y: 22, class: "node-label" });
      label.textContent = nodeLabel(node).length > 30 ? `${nodeLabel(node).slice(0, 29)}…` : nodeLabel(node);
      const text = svgElement("text", { x: 13, y: 44, class: "node-text" });
      textLines(nodeText(node)).forEach((line, index) => {
        const span = svgElement("tspan", { x: 13, dy: index === 0 ? 0 : 17 });
        span.textContent = line;
        text.append(span);
      });
      group.append(title, label, text);
      group.addEventListener("click", () => this.onInspect?.(node));
      group.addEventListener("keydown", event => {
        if (["Enter", " "].includes(event.key)) { event.preventDefault(); this.onInspect?.(node); }
        else if (["ArrowDown", "ArrowRight", "ArrowUp", "ArrowLeft"].includes(event.key)) {
          event.preventDefault();
          const direction = ["ArrowDown", "ArrowRight"].includes(event.key) ? 1 : -1;
          const next = this.orderedIds[(this.orderedIds.indexOf(id) + direction + this.orderedIds.length) % this.orderedIds.length];
          this.nodes.get(next).focus();
        }
      });
      this.nodes.set(id, group);
      this.svg.append(group);
    }
    this.svg.setAttribute("viewBox", `0 0 ${this.layout.width} ${this.layout.height}`);
    this.setZoom(.9, false);
    if (this.orderedIds.length) this.nodes.get(this.orderedIds[0]).setAttribute("tabindex", "0");
    return this.layout;
  }

  update(state, follow = true) {
    if (follow && this.current && this.current !== state.current && this.zoom < .55) this.setZoom(.8, false);
    this.current = state.current;
    const visited = new Set(state.visited), taken = new Set(state.taken);
    for (const [id, group] of this.nodes) {
      group.classList.toggle("visited", visited.has(id));
      group.classList.toggle("current", id === state.current);
      group.setAttribute("tabindex", id === state.current || (!this.nodes.has(state.current) && id === this.orderedIds[0]) ? "0" : "-1");
      if (id === state.current) group.setAttribute("aria-current", "step");
      else group.removeAttribute("aria-current");
    }
    for (const [id, path] of this.edgeElements) {
      path.classList.toggle("visited", taken.has(id));
      path.setAttribute("marker-end", taken.has(id) ? "url(#map-arrow-visited)" : "url(#map-arrow)");
    }
    if (follow) {
      cancelAnimationFrame(this.focusFrame);
      this.focusFrame = requestAnimationFrame(() => this.focusCurrent());
    }
  }

  setZoom(value, retainCenter = true) {
    if (!this.layout) return;
    const previous = this.zoom;
    this.zoom = Math.max(.00001, Math.min(1.6, value));
    this.svg.setAttribute("width", Math.ceil(this.layout.width * this.zoom));
    this.svg.setAttribute("height", Math.ceil(this.layout.height * this.zoom));
    if (retainCenter && previous) {
      const ratio = this.zoom / previous;
      this.viewport.scrollLeft = (this.viewport.scrollLeft + this.viewport.clientWidth / 2) * ratio - this.viewport.clientWidth / 2;
      this.viewport.scrollTop = (this.viewport.scrollTop + this.viewport.clientHeight / 2) * ratio - this.viewport.clientHeight / 2;
    }
    this.onZoom?.(Math.round(this.zoom * 100));
  }

  fit() {
    if (!this.layout || !this.viewport.clientWidth) return;
    this.setZoom(Math.min((this.viewport.clientWidth - 10) / this.layout.width, (this.viewport.clientHeight - 10) / this.layout.height, 1.1), false);
    this.viewport.scrollTo({ left: 0, top: 0, behavior: "auto" });
  }

  focusCurrent() {
    const position = this.layout?.positions.get(this.current);
    if (!position || !this.viewport.clientWidth) return;
    this.viewport.scrollTo({
      left: Math.max(0, (position.x + CARD_WIDTH / 2) * this.zoom - this.viewport.clientWidth / 2),
      top: Math.max(0, (position.y + CARD_HEIGHT / 2) * this.zoom - this.viewport.clientHeight * .34),
      behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth"
    });
  }
}
