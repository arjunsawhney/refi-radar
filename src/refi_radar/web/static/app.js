// Thin client over the public API. All business logic lives in the Python engine.

import { stackedBarChart } from "./charts.js";
import "./tooltip.js";

const SHOCKS = ["rate_shock_bps", "cap_rate_shock_bps", "office_vacancy_shock_pp", "noi_haircut_pct"];
const BUCKETS = ["refi_ready", "equity_gap", "recap_required", "distressed"];
const STATUS = {
  refi_ready: {
    label: "Ready to Refinance",
    meaning: "A new loan fully repays the existing loan. Easy win: pitch a refinance.",
  },
  equity_gap: {
    label: "Small Shortfall",
    meaning: "New loan falls short of the existing loan by up to 10%. Owner can likely add cash or take a small partner.",
  },
  recap_required: {
    label: "Needs New Capital",
    meaning: "Shortfall over 10% of the existing loan. Owner needs an outside investor (a 'recap') or a loan extension.",
  },
  distressed: {
    label: "Distressed",
    meaning: "Existing loan is more than the property is worth, or income no longer covers its payments. Likely a sale or restructuring.",
  },
};
const PROPERTY_TYPES = ["office", "multifamily", "industrial", "retail", "hotel"];
const TOP_N = 25;

const state = {
  presets: {},
  contacts: {},
  assessments: [],
  lastSummary: null,
  requestSeq: 0,
  hasRendered: false,
  filter: null, // {chart: "wall"|"types", label, seriesKey, period?, property_type?} from a clicked bar segment
};

const $ = (id) => document.getElementById(id);
const css = (name) => getComputedStyle(document.documentElement).getPropertyValue(`--${name}`).trim();
const money = (v) => (Math.abs(v) >= 1e9 ? `$${(v / 1e9).toFixed(2)}B` : `$${(v / 1e6).toFixed(1)}M`);
const pct = (v, digits = 0) => `${(v * 100).toFixed(digits)}%`;
const titleCase = (s) => s.charAt(0).toUpperCase() + s.slice(1);
const monthYear = (iso) =>
  new Date(`${iso}T00:00:00`).toLocaleDateString("en-US", { month: "short", year: "numeric" });
const escapeHtml = (s) =>
  String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
const pill = (bucket) =>
  `<span class="pill ${bucket}" data-tip="${escapeHtml(STATUS[bucket].meaning)}">${STATUS[bucket].label}</span>`;
const tel = (phone) => `tel:${phone.replace(/[^\d+]/g, "")}`;

const formatShock = {
  rate_shock_bps: (v) => `${v > 0 ? "+" : ""}${v} bps`,
  cap_rate_shock_bps: (v) => `${v > 0 ? "+" : ""}${v} bps`,
  office_vacancy_shock_pp: (v) => `${Math.round(v * 100)}%`,
  noi_haircut_pct: (v) => `${Math.round(v * 100)}%`,
};

async function api(path, options = {}) {
  const res = await fetch(path, { headers: { "Content-Type": "application/json" }, ...options });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(typeof body.detail === "string" ? body.detail : `${res.status} ${res.statusText}`);
  }
  return res.json();
}

function showError(message) {
  $("error-banner").textContent = message;
  $("error-banner").classList.toggle("hidden", !message);
}

function debounce(fn, ms) {
  let t;
  return (...args) => {
    clearTimeout(t);
    t = setTimeout(() => fn(...args), ms);
  };
}

function sliderValues() {
  return Object.fromEntries(SHOCKS.map((k) => [k, Number($(k).value)]));
}

function setSliders(scenario) {
  for (const k of SHOCKS) {
    $(k).value = scenario[k];
    $(`${k}-out`).textContent = formatShock[k](Number(scenario[k]));
  }
}

function selectedTypes() {
  return [...document.querySelectorAll("#type-filters input:checked")].map((el) => el.value);
}

function buildRequest() {
  const preset = state.presets[$("scenario").value];
  const sliders = sliderValues();
  const changed = SHOCKS.some((k) => Math.abs(sliders[k] - preset[k]) > 1e-9);
  const types = selectedTypes();
  return {
    scenario: preset.name,
    overrides: changed ? sliders : null,
    property_types: types.length === PROPERTY_TYPES.length ? null : types,
    top_n: TOP_N,
    call_list_filter: state.filter
      ? { bucket: state.filter.seriesKey, period: state.filter.period ?? null, property_type: state.filter.property_type ?? null }
      : null,
  };
}

async function selectSegment(chart, sel, extra) {
  const same = state.filter && state.filter.chart === chart && state.filter.label === sel.label &&
    state.filter.seriesKey === sel.seriesKey;
  state.filter = same ? null : { chart, label: sel.label, seriesKey: sel.seriesKey, ...extra };
  await refresh();
  const top = $("call-card").getBoundingClientRect().top;
  if (state.filter && top > window.innerHeight * 0.6) $("call-card").scrollIntoView({ behavior: "smooth", block: "start" });
}

function clearFilter() {
  state.filter = null;
  refresh();
}

function renderListStatus(data) {
  const shown = data.assessments.length;
  const total = data.assessments_total ?? shown;
  const f = state.filter;
  if (!f) {
    $("list-filter").innerHTML = `<span class="hint">Showing top ${shown} of ${total} loans. Click any bar segment above to filter this list.</span>`;
    return;
  }
  const what = f.chart === "wall" ? f.label : titleCase(f.property_type);
  $("list-filter").innerHTML =
    `<span class="filter-chip">Filtered: ${escapeHtml(what)} · ${STATUS[f.seriesKey].label}` +
    ` <button type="button" id="clear-filter" aria-label="Clear filter">✕</button></span>` +
    `<span class="hint">${total === 0 ? "No loans match in this scenario." : `Showing ${shown} of ${total} matching loans.`}</span>`;
  $("clear-filter").addEventListener("click", clearFilter);
}

async function refresh() {
  const seq = ++state.requestSeq;
  const request = buildRequest();
  if (request.property_types && request.property_types.length === 0) {
    showError("Select at least one property type.");
    return;
  }
  try {
    const data = await api("/api/analyze", { method: "POST", body: JSON.stringify(request) });
    if (seq !== state.requestSeq) return; // a newer request superseded this one
    showError("");
    render(data);
  } catch (err) {
    showError(`Analysis failed: ${err.message}`);
  }
}

function render(data) {
  const s = data.summary;
  $("scenario-label").textContent = data.scenario.label || data.scenario.name;
  $("scenario-desc").textContent = data.scenario.description;

  $("kpi-balance").textContent = money(s.total_balance);
  $("kpi-loans").textContent = `${s.loans_in_window} loans due by ${state.windowEndLabel}`;
  $("kpi-atrisk").textContent = money(s.at_risk_balance);
  $("kpi-atrisk-pct").textContent = `${pct(s.at_risk_pct)} of the loans due soon`;
  $("kpi-gap").textContent = money(s.total_refi_gap);
  $("kpi-fees").textContent = money(s.indicative_fee_pool);

  const c = data.comparison;
  const mig = $("kpi-migration");
  if (c) {
    mig.textContent = `${c.loans_worsened} loans worse`;
    mig.classList.toggle("bad", c.loans_worsened > 0);
    $("kpi-migration-sub").textContent =
      `than Base Case · shortfall ${c.refi_gap_delta >= 0 ? "+" : ""}${money(c.refi_gap_delta)}`;
  } else {
    mig.textContent = "Today's market";
    mig.classList.remove("bad");
    $("kpi-migration-sub").textContent = "pick a what-if scenario to compare";
  }

  $("table-scenario").textContent = `Results for: ${data.scenario.label || data.scenario.name}`;

  state.lastSummary = s;
  renderCharts(s);
  renderTable(data.assessments);
  renderListStatus(data);
  if (state.hasRendered) flashUpdated();
  state.hasRendered = true;
}

// Brief highlight so users notice that everything below the controls was recalculated.
function flashUpdated() {
  for (const el of document.querySelectorAll(".kpi, .chart-card, #call-card")) {
    el.classList.remove("flash");
    void el.offsetWidth; // restart the CSS animation
    el.classList.add("flash");
  }
}

function renderCharts(summary) {
  const axisMoney = (v) => (v >= 1e9 ? `$${(v / 1e9).toFixed(1)}B` : `$${(v / 1e6).toFixed(0)}M`);
  const wallLabels = summary.maturity_wall.map((p) => p.period);
  const f = state.filter;
  stackedBarChart($("chart-wall"), {
    labels: wallLabels,
    selected: f?.chart === "wall" ? { index: wallLabels.indexOf(f.label), seriesKey: f.seriesKey } : null,
    onSelect: (sel) => selectSegment("wall", sel, { period: sel.label }),
    series: BUCKETS.map((b) => ({
      key: b,
      label: STATUS[b].label,
      color: css(b),
      values: summary.maturity_wall.map((p) => p.balance_by_bucket[b]),
    })),
    format: axisMoney,
    tooltip: ({ label, series, value, total }) =>
      `<strong>${escapeHtml(label)}</strong><br>${escapeHtml(series)}: <strong>${money(value)}</strong>` +
      `<br><span class="muted">Total due this quarter: ${money(total)}</span><br><em>Click to list these loans</em>`,
  });

  // Same four status colors as the quarterly chart, with the most troubled status nearest the axis.
  const types = summary.by_property_type;
  const typeSeries = BUCKETS.map((b) => ({
    key: b,
    label: STATUS[b].label,
    color: css(b),
    values: types.map((t) => t.balance_by_bucket[b]),
  }));
  const typeLabels = types.map((t) => titleCase(t.property_type));
  stackedBarChart($("chart-types"), {
    labels: typeLabels,
    selected: f?.chart === "types" ? { index: typeLabels.indexOf(f.label), seriesKey: f.seriesKey } : null,
    onSelect: (sel) => selectSegment("types", sel, { property_type: types[sel.index].property_type }),
    series: [...typeSeries].reverse(),
    legendSeries: typeSeries,
    horizontal: true,
    format: axisMoney,
    annotate: (i) => `${pct(types[i].balance ? types[i].at_risk_balance / types[i].balance : 0)} hard to refi`,
    tooltip: ({ index, label, series, value, total }) =>
      `<strong>${escapeHtml(label)}</strong><br>${escapeHtml(series)}: <strong>${money(value)}</strong> ` +
      `(${pct(total ? value / total : 0)})<br><span class="muted">Total due: ${money(total)} · ` +
      `hard to refinance: ${money(types[index].at_risk_balance)}</span><br><em>Click to list these loans</em>`,
  });
}

function dueCell(a) {
  if (a.past_maturity) return `${monthYear(a.maturity_date)}<br><span class="badge-late">Past due</span>`;
  const m = a.months_to_maturity;
  return `${monthYear(a.maturity_date)}<br><span class="hint">${m === 0 ? "this month" : `in ${m} mo`}</span>`;
}

// Mirrors LENDER_LABELS in assessment.py.
const LENDER_LABELS = {
  CMBS: "CMBS special servicer",
  Bank: "The bank",
  "Life Co": "Life insurance lender",
  "Debt Fund": "The debt fund",
  Agency: "Agency lender",
};
const lenderLabel = (lenderType) => LENDER_LABELS[lenderType] ?? `${lenderType} lender`;

function contactCell(a) {
  const c = state.contacts[a.sponsor];
  if (a.call_target === "lender") {
    const owner = c ? `${escapeHtml(c.contact_name)}<br><a href="${tel(c.phone)}" class="phone">${escapeHtml(c.phone)}</a>` : escapeHtml(a.sponsor);
    return `<strong>The lender</strong><br>${escapeHtml(lenderLabel(a.lender_type))}` +
      `<br><span class="hint">Owner: ${owner}</span>`;
  }
  if (!c) return `<span class="hint">${escapeHtml(a.sponsor)}<br>no CRM contact</span>`;
  return `${escapeHtml(c.contact_name)}<br><a href="${tel(c.phone)}" class="phone">${escapeHtml(c.phone)}</a>` +
    `<br><span class="hint">${escapeHtml(a.sponsor)}</span>`;
}

function renderTable(assessments) {
  state.assessments = assessments;
  const rows = assessments.map((a, i) => {
    const p = a.priority;
    const scoreTip = `Urgency ${p.urgency.toFixed(2)} × 50 + size ${p.size.toFixed(2)} × 30 + advisory need ${p.advisory_need.toFixed(2)} × 20`;
    return `
    <tr data-index="${i}" tabindex="0">
      <td>${i + 1}</td>
      <td><span class="score" data-tip="${escapeHtml(scoreTip)}">${p.score.toFixed(0)}</span></td>
      <td><strong>${escapeHtml(a.property_name)}</strong><br><span class="hint">${escapeHtml(a.market)}</span></td>
      <td>${titleCase(a.property_type)}</td>
      <td>${dueCell(a)}</td>
      <td class="num">${money(a.balloon_balance)}</td>
      <td class="num">${money(a.max_refi_loan)}</td>
      <td class="num">${a.refi_gap > 0 ? `<strong>${money(a.refi_gap)}</strong>` : "None"}</td>
      <td>${pill(a.bucket)}</td>
      <td class="next-step">${escapeHtml(a.next_step)}</td>
      <td>${contactCell(a)}</td>
    </tr>`;
  });
  document.querySelector("#call-list tbody").innerHTML = rows.join("");
}

function contactCard(a) {
  if (a.call_target === "lender") {
    return `
      <div class="contact-card lender">
        <div><strong>Call the lender: ${escapeHtml(lenderLabel(a.lender_type))}</strong></div>
        <div class="hint">The property is worth less than the existing loan, so the lender, not the owner, decides what happens next.
          In production the lender's contact comes from the CRM by lender name.</div>
      </div>
      <p class="hint" style="margin-top:10px">Owner, for context:</p>
      ${ownerCard(a)}`;
  }
  return ownerCard(a);
}

function ownerCard(a) {
  const c = state.contacts[a.sponsor];
  if (!c) {
    return `<div class="contact-card"><strong>${escapeHtml(a.sponsor)}</strong><p class="hint">No contact in CRM. Research the decision-maker before calling.</p></div>`;
  }
  return `
    <div class="contact-card">
      <div><strong>${escapeHtml(c.contact_name)}</strong>, ${escapeHtml(c.title)}</div>
      <div class="hint">${escapeHtml(a.sponsor)} (owner / borrower)</div>
      <div class="contact-links">
        <a href="${tel(c.phone)}">📞 ${escapeHtml(c.phone)}</a>
        <a href="mailto:${escapeHtml(c.email)}">✉️ ${escapeHtml(c.email)}</a>
      </div>
      <div class="hint">Newmark relationship owner: ${escapeHtml(c.coverage_broker)}</div>
    </div>`;
}

function showDetail(a) {
  const p = a.priority;
  $("detail-body").innerHTML = `
    <h3>${escapeHtml(a.property_name)}</h3>
    <p class="hint">${titleCase(a.property_type)} · ${escapeHtml(a.market)} · loan ${escapeHtml(a.loan_id)}</p>
    <p>${pill(a.bucket)} <span class="drawer-step">${escapeHtml(a.next_step)}</span></p>

    <h2>Who to call</h2>
    ${contactCard(a)}

    <h2>Talking points</h2>
    <ol class="talking-points">${a.talking_points.map((t) => `<li>${escapeHtml(t)}</li>`).join("")}</ol>

    <h2>Key numbers</h2>
    <dl>
      <dt>Loan due</dt><dd>${monthYear(a.maturity_date)}${a.past_maturity ? " (past due)" : ` (in ${a.months_to_maturity} mo)`}</dd>
      <dt>Existing loan (owed)</dt><dd>${money(a.balloon_balance)}</dd>
      <dt>Property value today</dt><dd>${money(a.stressed_value)}</dd>
      <dt>Max new loan today</dt><dd>${money(a.max_refi_loan)}</dd>
      <dt>Shortfall (existing − new)</dt><dd>${a.refi_gap > 0 ? `${money(a.refi_gap)} (${pct(a.refi_gap_pct)})` : "None"}</dd>
      <dt>Yearly income (NOI)</dt><dd>${money(a.stressed_noi)}</dd>
      <dt>Occupancy</dt><dd>${pct(a.stressed_occupancy)}</dd>
      <dt>Interest rate on a new loan</dt><dd>${pct(a.refi_rate, 2)}</dd>
    </dl>

    <h2>Why this status</h2>
    <ul>${a.reasons.map((r) => `<li>${escapeHtml(r)}</li>`).join("")}</ul>

    <details class="analyst">
      <summary>Underwriting details (for analysts)</summary>
      <p class="hint">${escapeHtml(a.recommended_action)}</p>
      <dl>
        <dt>Loan-to-value at maturity</dt><dd>${pct(a.ltv_at_maturity)}</dd>
        <dt>DSCR, current loan</dt><dd>${a.in_place_dscr.toFixed(2)}x</dd>
        <dt>DSCR, new loan at today's rate</dt><dd>${a.refi_dscr.toFixed(2)}x</dd>
        <dt>Debt yield</dt><dd>${pct(a.debt_yield, 1)}</dd>
        <dt>Limiting lender test</dt><dd>${escapeHtml(a.binding_constraint)}</dd>
        <dt>Current lender type</dt><dd>${escapeHtml(a.lender_type)}</dd>
        <dt>Est. Newmark fee</dt><dd>${money(a.indicative_fee)}</dd>
        <dt>Priority</dt><dd>${p.score.toFixed(0)} / 100</dd>
        <dt>· urgency (50%)</dt><dd>${p.urgency.toFixed(2)}</dd>
        <dt>· size (30%)</dt><dd>${p.size.toFixed(2)}</dd>
        <dt>· advisory need (20%)</dt><dd>${p.advisory_need.toFixed(2)}</dd>
      </dl>
    </details>`;
  $("help").classList.add("hidden");
  $("detail").classList.remove("hidden");
}

function renderStatusKey() {
  const item = (b) => `<div class="key-item">${pill(b)}<span>${escapeHtml(STATUS[b].meaning)}</span></div>`;
  const group = (title, hint, buckets) =>
    `<div class="key-group"><div class="key-group-title">${title} <span class="hint">${hint}</span></div>` +
    `<div class="key-group-items">${buckets.map(item).join("")}</div></div>`;
  const html =
    group("Manageable", "owner can refinance alone", ["refi_ready", "equity_gap"]) +
    group("Hard to refinance", "needs outside help", ["recap_required", "distressed"]);
  $("status-key").innerHTML = html;
  $("help-status").innerHTML = html;
}

function closeDrawers() {
  $("detail").classList.add("hidden");
  $("help").classList.add("hidden");
}

async function init() {
  renderStatusKey();
  try {
    const [health, presets, contacts] = await Promise.all([
      api("/api/health"),
      api("/api/scenarios"),
      api("/api/contacts"),
    ]);
    state.windowEndLabel = monthYear(health.window_end);
    $("tip-window").dataset.tip =
      `Loans that must be repaid between ${monthYear(health.as_of)} and ${state.windowEndLabel} ` +
      `(the next ${health.horizon_months} months), plus loans already past due. Later maturities are left out.`;
    state.contacts = Object.fromEntries(contacts.map((c) => [c.sponsor, c]));

    const select = $("scenario");
    for (const p of presets) {
      state.presets[p.name] = p;
      select.add(new Option(p.label || p.name, p.name));
    }
    select.value = "base" in state.presets ? "base" : presets[0].name;
    setSliders(state.presets[select.value]);

    $("type-filters").innerHTML = PROPERTY_TYPES.map(
      (t) => `<label><input type="checkbox" value="${t}" checked /> ${titleCase(t)}</label>`,
    ).join("");
  } catch (err) {
    showError(`Could not reach the API: ${err.message}`);
    return;
  }

  const debounced = debounce(refresh, 200);
  for (const k of SHOCKS) {
    $(k).addEventListener("input", () => {
      $(`${k}-out`).textContent = formatShock[k](Number($(k).value));
      debounced();
    });
  }
  $("scenario").addEventListener("change", () => {
    setSliders(state.presets[$("scenario").value]);
    refresh();
  });
  $("reset").addEventListener("click", () => {
    setSliders(state.presets[$("scenario").value]);
    refresh();
  });
  $("type-filters").addEventListener("change", () => {
    if (state.filter?.property_type && !selectedTypes().includes(state.filter.property_type)) state.filter = null;
    refresh();
  });

  const tbody = document.querySelector("#call-list tbody");
  const openRow = (e) => {
    if (e.target.closest("a")) return; // let phone links work without opening the drawer
    const row = e.target.closest("tr");
    if (row) showDetail(state.assessments[Number(row.dataset.index)]);
  };
  tbody.addEventListener("click", openRow);
  tbody.addEventListener("keydown", (e) => e.key === "Enter" && openRow(e));

  $("help-open").addEventListener("click", () => {
    $("detail").classList.add("hidden");
    $("help").classList.remove("hidden");
  });
  for (const btn of document.querySelectorAll(".drawer-close")) btn.addEventListener("click", closeDrawers);
  document.addEventListener("keydown", (e) => e.key === "Escape" && closeDrawers());
  window.addEventListener("resize", debounce(() => state.lastSummary && renderCharts(state.lastSummary), 150));

  refresh();
}

init();
