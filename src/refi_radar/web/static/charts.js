// Minimal dependency-free stacked bar charts rendered as SVG.
// Chosen over a CDN chart library so the dashboard works offline and behind corporate proxies.
// Hover text is provided through data-tip-html and rendered by tooltip.js.

const SVG_NS = "http://www.w3.org/2000/svg";

function el(name, attrs = {}, text) {
  const node = document.createElementNS(SVG_NS, name);
  for (const [k, v] of Object.entries(attrs)) node.setAttribute(k, v);
  if (text !== undefined) node.textContent = text;
  return node;
}

function niceMax(value) {
  if (value <= 0) return 1;
  const magnitude = 10 ** Math.floor(Math.log10(value));
  const step = [1, 2, 2.5, 5, 10].find((s) => s * magnitude >= value / 4) * magnitude;
  return Math.ceil(value / step) * step;
}

function renderLegend(container, series) {
  const legend = document.createElement("div");
  legend.className = "chart-legend";
  for (const s of series) {
    const item = document.createElement("span");
    const swatch = document.createElement("i");
    swatch.style.background = s.color;
    item.append(swatch, document.createTextNode(s.label));
    legend.append(item);
  }
  container.append(legend);
}

/**
 * @param {HTMLElement} container
 * @param {{labels: string[], series: {key?: string, label: string, color: string, values: number[]}[],
 *          horizontal?: boolean, format: (v: number) => string, height?: number,
 *          tooltip?: (ctx: {index: number, label: string, series: string, value: number, total: number}) => string,
 *          annotate?: (index: number) => string, legendSeries?: {label: string, color: string}[],
 *          onSelect?: (sel: {index: number, label: string, seriesKey: string}) => void,
 *          selected?: {index: number, seriesKey: string} | null}} spec   annotate: text drawn at the end of each horizontal bar
 */
export function stackedBarChart(container, spec) {
  const { labels, series, horizontal = false, format } = spec;
  const width = container.clientWidth || 600;
  const height = spec.height ?? (horizontal ? Math.max(160, labels.length * 38 + 40) : 260);
  const pad = horizontal
    ? { top: 8, right: spec.annotate ? 128 : 16, bottom: 28, left: 92 }
    : { top: 12, right: 12, bottom: 32, left: 64 };
  const plotW = width - pad.left - pad.right;
  const plotH = height - pad.top - pad.bottom;

  const totals = labels.map((_, i) => series.reduce((sum, s) => sum + (s.values[i] || 0), 0));
  const max = niceMax(Math.max(0, ...totals));
  const scale = (v) => (v / max) * (horizontal ? plotW : plotH);

  container.replaceChildren();
  const svg = el("svg", { viewBox: `0 0 ${width} ${height}`, preserveAspectRatio: "xMinYMin meet", role: "img" });
  const g = el("g", { transform: `translate(${pad.left},${pad.top})` });
  svg.append(g);

  for (let t = 0; t <= 4; t++) {
    const v = (max / 4) * t;
    const pos = scale(v);
    if (horizontal) {
      g.append(el("line", { x1: pos, x2: pos, y1: 0, y2: plotH, class: "grid" }));
      g.append(el("text", { x: pos, y: plotH + 18, "text-anchor": "middle", class: "tick" }, format(v)));
    } else {
      g.append(el("line", { x1: 0, x2: plotW, y1: plotH - pos, y2: plotH - pos, class: "grid" }));
      g.append(el("text", { x: -8, y: plotH - pos + 4, "text-anchor": "end", class: "tick" }, format(v)));
    }
  }

  const band = (horizontal ? plotH : plotW) / Math.max(1, labels.length);
  const barSize = Math.min(48, band * 0.7);
  labels.forEach((label, i) => {
    const center = band * i + band / 2;
    let offset = 0;
    for (const s of series) {
      const v = s.values[i] || 0;
      if (v <= 0) continue;
      const len = scale(v);
      const rect = horizontal
        ? el("rect", { x: offset, y: center - barSize / 2, width: len, height: barSize, fill: s.color })
        : el("rect", { x: center - barSize / 2, y: plotH - offset - len, width: barSize, height: len, fill: s.color });
      const ctx = { index: i, label, series: s.label, value: v, total: totals[i] };
      rect.dataset.tipHtml = spec.tooltip ? spec.tooltip(ctx) : `${label} · ${s.label}: ${format(v)}`;
      if (spec.onSelect) {
        const sel = { index: i, label, seriesKey: s.key ?? s.label };
        rect.setAttribute("tabindex", "0");
        rect.setAttribute("role", "button");
        rect.addEventListener("click", () => spec.onSelect(sel));
        rect.addEventListener("keydown", (e) => (e.key === "Enter" || e.key === " ") && spec.onSelect(sel));
      }
      if (spec.selected) {
        const isSelected = spec.selected.index === i && spec.selected.seriesKey === (s.key ?? s.label);
        rect.classList.add(isSelected ? "selected" : "dimmed");
      }
      g.append(rect);
      offset += len;
    }
    if (horizontal) {
      g.append(el("text", { x: -8, y: center + 4, "text-anchor": "end", class: "label" }, label));
      if (spec.annotate) g.append(el("text", { x: offset + 8, y: center + 4, class: "annotation" }, spec.annotate(i)));
    } else {
      g.append(el("text", { x: center, y: plotH + 18, "text-anchor": "middle", class: "label" }, label));
    }
  });

  container.append(svg);
  renderLegend(container, spec.legendSeries ?? series);
}
