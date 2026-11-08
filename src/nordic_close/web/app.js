"use strict";

// Everything is built with textContent so data from the API can never be read as HTML.

const state = { period: null, tab: "overview", key: "" };
try { state.key = sessionStorage.getItem("nc_key") || ""; } catch (e) { /* storage may be blocked */ }

const main = document.getElementById("main");
const periodSelect = document.getElementById("period");
const pill = document.getElementById("status-pill");
const runButton = document.getElementById("run");
const exportButton = document.getElementById("export");
const keyForm = document.getElementById("keyform");

const SVG = "http://www.w3.org/2000/svg";

function el(tag, attrs, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === null || v === undefined || v === false) continue;
    if (k === "class") node.className = v;
    else node.setAttribute(k, v === true ? "" : v);
  }
  for (const c of children.flat()) {
    if (c === null || c === undefined) continue;
    node.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return node;
}

function svg(tag, attrs, text) {
  const node = document.createElementNS(SVG, tag);
  for (const [k, v] of Object.entries(attrs || {})) node.setAttribute(k, v);
  if (text !== undefined) node.textContent = text;
  return node;
}

const nf0 = new Intl.NumberFormat("en-GB", { maximumFractionDigits: 0 });
const nf2 = new Intl.NumberFormat("en-GB", { minimumFractionDigits: 2, maximumFractionDigits: 2 });

function num(value, digits = 0) {
  if (value === null || value === undefined) return "n/a";
  const n = Number(value);
  if (Number.isNaN(n)) return String(value);
  return (digits === 2 ? nf2 : nf0).format(n);
}
function millions(value) {
  const n = Number(value);
  return (n / 1e6).toLocaleString("en-GB", { minimumFractionDigits: 1, maximumFractionDigits: 1 }) + "m";
}
function kpiText(k) {
  if (k.value === null) return "n/a";
  const n = Number(k.value);
  if (k.unit === "percent") return (n * 100).toFixed(1) + "%";
  if (k.unit === "times") return n.toFixed(2) + "x";
  if (k.unit === "days") return n.toFixed(0) + " days";
  return millions(n);
}

async function api(path, options = {}) {
  const headers = Object.assign({}, options.headers || {});
  if (state.key) headers["X-API-Key"] = state.key;
  const response = await fetch(path, Object.assign({}, options, { headers }));
  if (response.status === 401) {
    keyForm.hidden = false;
    throw new Error("An API key is required.");
  }
  if (!response.ok) {
    let detail = response.statusText;
    try { detail = (await response.json()).detail || detail; } catch (e) { /* not JSON */ }
    throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
  }
  return response;
}
async function getJson(path) { return (await api(path)).json(); }

function table(caption, headers, rows, options = {}) {
  const head = el("tr", null, headers.map((h, i) => el("th", { scope: "col", class: i === 0 || options.textCols?.includes(i) ? "text" : null }, h)));
  const body = rows.map((r) => {
    const cells = r.cells.map((c, i) => {
      const isNode = c instanceof Node;
      const cls = [];
      if (options.textCols?.includes(i)) cls.push("text");
      if (!isNode && typeof c === "string" && c.startsWith("-") && /\d/.test(c)) cls.push("neg");
      return el(i === 0 ? "th" : "td", { scope: i === 0 ? "row" : null, class: cls.join(" ") || null }, c);
    });
    return el("tr", { class: r.sub ? "sub" : null }, cells);
  });
  return el("div", { class: "tablewrap" }, el("table", null, el("caption", null, caption), el("thead", null, head), el("tbody", null, body)));
}

function tag(kind, text) { return el("span", { class: "tag " + kind }, text); }

// ----- charts ---------------------------------------------------------------------------

function barChart(title, items, currency) {
  const width = 520, rowH = 30, left = 70, right = 90, top = 8;
  const height = top + items.length * rowH + 8;
  const values = items.map((i) => Number(i.value));
  const max = Math.max(1, ...values.map(Math.abs));
  const chart = svg("svg", { viewBox: `0 0 ${width} ${height}`, width: "100%", role: "img", "aria-label": title });
  items.forEach((item, idx) => {
    const y = top + idx * rowH;
    const v = Number(item.value);
    const w = Math.max(1, (Math.abs(v) / max) * (width - left - right));
    chart.append(svg("text", { x: 0, y: y + 18 }, item.label));
    chart.append(svg("rect", { x: left, y: y + 4, width: w, height: 18, rx: 3, fill: v < 0 ? "var(--bar-neg)" : "var(--bar)" }));
    chart.append(svg("text", { x: left + w + 6, y: y + 18 }, millions(v)));
  });
  return el("div", { class: "card" }, el("h3", null, `${title} (${currency})`), chart);
}

// ----- views ----------------------------------------------------------------------------

async function viewOverview() {
  const p = state.period;
  const [summary, kpis, ents] = await Promise.all([
    getJson(`/api/controls/summary?period=${p}`),
    getJson(`/api/reports/kpis?period=${p}`),
    getJson(`/api/reports/entities?period=${p}`),
  ]);
  setPill(summary.status);
  const failed = summary.gates.filter((g) => g.status === "fail");
  const banner = el("div", { class: "banner " + summary.status },
    el("strong", null, summary.status === "ready" ? "Ready to sign off" : "Blocked"),
    summary.status === "ready"
      ? "Every control passed for this period."
      : el("ul", null, failed.map((g) => el("li", null, `${g.label}: ${g.reason}`))));
  const tiles = el("div", { class: "grid" }, kpis.kpis.map((k) =>
    el("div", { class: "tile" }, el("div", { class: "k" }, k.label), el("div", { class: "v" }, kpiText(k)))));
  const cur = kpis.currency;
  const charts = el("div", { class: "row2" },
    barChart("Revenue by entity, month", ents.entities.map((e) => ({ label: e.entity, value: e.revenue_group })), cur),
    barChart("Net income by entity, month", ents.entities.map((e) => ({ label: e.entity, value: e.net_income_group })), cur));
  main.replaceChildren(banner, el("h2", null, `Key figures in ${cur}`), tiles, el("h2", null, "Entity contribution"), charts);
}

async function viewStatements() {
  const p = state.period;
  const [is, bs] = await Promise.all([
    getJson(`/api/reports/income-statement?period=${p}`),
    getJson(`/api/reports/balance-sheet?period=${p}`),
  ]);
  const cur = is.currency;
  const isRows = is.rows.map((r) => ({ sub: r.subtotal, cells: [r.label, num(r.values.month), num(r.values.prior_month), num(r.values.ytd)] }));
  const section = (name, rows, total) => [
    { sub: false, cells: [el("strong", null, name), "", ""] },
    ...rows.map((r) => ({ sub: false, cells: [r.label, num(r.values.closing), num(r.values.prior_closing)] })),
    { sub: true, cells: [total.label, num(total.values.closing), num(total.values.prior_closing)] },
  ];
  const bsRows = [
    ...section("Assets", bs.assets, bs.total_assets),
    ...section("Equity", bs.equity, bs.total_equity),
    ...section("Liabilities", bs.liabilities, bs.total_liabilities),
    { sub: true, cells: [bs.total_equity_liabilities.label, num(bs.total_equity_liabilities.values.closing), num(bs.total_equity_liabilities.values.prior_closing)] },
  ];
  main.replaceChildren(
    el("h2", null, `Income statement (${cur})`),
    table(`Consolidated income statement for ${p}`, ["", "Month", "Prior month", "Year to date"], isRows),
    el("h2", null, `Balance sheet (${cur})`),
    table(`Consolidated balance sheet at ${p}`, ["", "Closing", "Prior closing"], bsRows),
    el("p", { class: "note" }, `Check, assets less equity and liabilities: ${num(bs.check, 2)}`));
}

async function viewEntities() {
  const e = await getJson(`/api/reports/entities?period=${state.period}`);
  const c = e.currency;
  const rows = e.entities.map((x) => ({ cells: [
    `${x.entity} ${x.name}`, x.currency, Number(x.closing_rate).toFixed(4), Number(x.average_rate).toFixed(4),
    num(x.revenue_local), num(x.revenue_group), num(x.net_income_local), num(x.net_income_group),
    num(x.net_assets_group), num(x.translation_reserve)] }));
  main.replaceChildren(
    el("h2", null, "Contribution by entity"),
    table(`Entities before eliminations, translated to ${c}`,
      ["Entity", "Cur", "Closing rate", "Average rate", "Revenue (local)", `Revenue (${c})`, "Net income (local)", `Net income (${c})`, `Net assets (${c})`, "Translation reserve"], rows,
      { textCols: [1] }),
    el("p", { class: "note" }, "Revenue includes intercompany sales, which are eliminated in the consolidated statements."));
}

async function viewControls() {
  const p = state.period;
  const [summary, ic, rec, flux, cal] = await Promise.all([
    getJson(`/api/controls/summary?period=${p}`),
    getJson(`/api/controls/intercompany?period=${p}`),
    getJson(`/api/controls/reconciliations?period=${p}`),
    getJson(`/api/controls/flux?period=${p}`),
    getJson("/api/controls/calendar").catch(() => null),
  ]);
  setPill(summary.status);

  const gates = table("Gates", ["Control", "Result", "Reason"],
    summary.gates.map((g) => ({ cells: [g.label, tag(g.status, g.status), g.reason || ""] })), { textCols: [1, 2] });

  const pairs = table("Intercompany pairs, EUR", ["Pair", "Receivable", "Payable", "Balance difference", "P&L difference", "Result"],
    ic.pairs.map((x) => ({ cells: [`${x.seller} to ${x.buyer}`, num(x.receivable_eur, 2), num(x.payable_eur, 2), num(x.bs_difference_eur, 2), num(x.pl_difference_eur, 2), tag(x.matches ? "pass" : "fail", x.matches ? "match" : "difference")] })),
    { textCols: [5] });
  const suggestions = ic.pairs.filter((x) => x.suggested_journal).map((x) => {
    const j = x.suggested_journal;
    return el("p", { class: "note" }, `Suggested journal in ${j.entity} dated ${j.date}: debit ${j.debit}, credit ${j.credit}, EUR ${j.amount_eur} (${j.amount_local} ${j.currency}), counterparty ${j.counterparty}.`);
  });

  const recCards = rec.reconciliations.map((r) => {
    const items = [
      ...r.outstanding.map((i) => ({ cells: ["In ledger, not at bank", i.date, i.ref, num(i.amount, 2), String(i.age_days)] })),
      ...r.unrecorded.map((i) => ({ cells: ["At bank, not in ledger", i.date, i.ref || i.description, num(i.amount, 2), String(i.age_days)] })),
      ...r.transpositions.map((t) => ({ cells: ["Digits transposed", t.ledger.date, t.ledger.ref, `${num(t.ledger.amount, 2)} vs ${num(t.bank.amount, 2)}`, `diff ${num(t.difference, 2)}`] })),
    ];
    const journals = r.journals.map((j) => el("li", null, `${j.note}: debit ${j.debit}, credit ${j.credit}, ${j.amount} (${j.reference})`));
    return el("div", { class: "card" },
      el("h3", null, `${r.entity} `, tag(r.passes ? "pass" : "fail", r.status.replace(/_/g, " "))),
      el("p", { class: "note" }, `Ledger ${num(r.ledger_closing, 2)}, bank ${num(r.bank_closing, 2)}, proof difference ${num(r.proof_difference, 2)}, ${r.matched} lines matched. Prepared by ${r.preparer}, reviewed by ${r.reviewer}.`),
      items.length ? table(`Open items for ${r.entity}`, ["Type", "Date", "Reference", "Amount", "Age (days)"], items, { textCols: [1, 2] }) : null,
      journals.length ? el("div", null, el("strong", null, "Journals needed"), el("ul", null, journals)) : null);
  });

  const fluxTable = flux.items.length
    ? table(`Material fluctuations (more than ${flux.policy.percent}% and ${num(flux.policy.absolute)})`,
        ["Line", "Current", "Prior", "Change", "Explanation"],
        flux.items.map((i) => ({ cells: [i.label, num(i.current), num(i.prior), num(i.change), el("span", null, i.ok ? tag("pass", "explained") : tag("fail", "needs explanation"), " ", i.explanation || i.problems.join(", "))] })),
        { textCols: [4] })
    : el("p", { class: "note" }, "No material fluctuations.");

  const calTable = cal ? table(`Close plan, critical path ends on workday ${cal.finish_workday} (target ${cal.target_workday})`,
    ["Task", "Owner", "Reviewer", "Start", "Finish", "Slack", "Critical"],
    cal.tasks.map((t) => ({ cells: [t.name, t.owner, t.reviewer, t.start, t.finish, t.slack, t.critical ? "yes" : ""] })),
    { textCols: [1, 2] }) : null;

  main.replaceChildren(
    el("h2", null, "Gates"), gates,
    el("h2", null, "Intercompany"), pairs, ...suggestions,
    el("h2", null, "Bank reconciliations"), ...recCards,
    el("h2", null, "Flux review"), fluxTable,
    el("h2", null, "Close calendar"), calTable);
}

async function viewVariance() {
  const p = state.period;
  const [m, y] = await Promise.all([
    getJson(`/api/reports/variance?period=${p}&view=month`),
    getJson(`/api/reports/variance?period=${p}&view=ytd`),
  ]);
  const build = (v, title) => {
    const rows = v.rows.map((r) => ({ cells: [r.label, num(r.actual), num(r.budget), num(r.variance), num(r.operating_effect), num(r.fx_effect)] }));
    rows.push({ sub: true, cells: ["Budgeted lines", num(v.total.actual), num(v.total.budget), num(v.total.variance), num(v.total.operating_effect), num(v.total.fx_effect)] });
    return table(`${title} (${v.currency})`, ["Line", "Actual", "Budget", "Variance", "Operating effect", "FX effect"], rows);
  };
  main.replaceChildren(
    el("h2", null, "Actual against budget"), build(m, `Month ${p}`), build(y, `Year to date to ${p}`),
    el("p", { class: "note" }, "Positive is favourable. Budget is translated at the rate on the opening date; the FX effect is the gain or loss from actuals translating at different rates."));
}

async function viewFx() {
  const p = state.period;
  const [fx, runs] = await Promise.all([getJson(`/api/fx?period=${p}`), getJson(`/api/close/runs?period=${p}`)]);
  const rates = table(`Rates to ${fx.presentation_currency}`, ["Entity", "Currency", "Closing", "Closing date", "Average", "Days", "Opening", "Opening date"],
    fx.rates.map((r) => ({ cells: [r.entity, r.currency, Number(r.closing).toFixed(6), r.closing_date, Number(r.average).toFixed(6), String(r.average_days), Number(r.opening).toFixed(6), r.opening_date] })),
    { textCols: [1, 3, 7] });
  const hist = runs.length ? table("Close runs", ["Run", "Started", "Status", "Input hash", "Output hash"],
    runs.map((r) => ({ cells: [`#${r.run_id}`, r.started_at, tag(r.status === "ready" ? "pass" : "fail", r.status), r.input_hash.slice(0, 12), r.output_hash.slice(0, 12)] })),
    { textCols: [1, 2, 3, 4] }) : el("p", { class: "note" }, "No runs stored for this period yet.");
  main.replaceChildren(el("h2", null, "Exchange rates used"), rates,
    el("p", { class: "note" }, "ECB euro reference rates. Cross rates are derived through EUR. Closing is the last rate on or before month end."),
    el("h2", null, "Run history"), hist);
}

const VIEWS = { overview: viewOverview, statements: viewStatements, entities: viewEntities, controls: viewControls, variance: viewVariance, fx: viewFx };

function setPill(status) {
  pill.className = "pill " + status;
  pill.textContent = status === "ready" ? "Ready" : "Blocked";
}

async function render() {
  main.replaceChildren(el("p", { class: "note" }, "Loading..."));
  try {
    await VIEWS[state.tab]();
  } catch (e) {
    main.replaceChildren(el("div", { class: "error", role: "alert" }, e.message));
  }
}

async function init() {
  try {
    const periods = await getJson("/api/periods");
    periodSelect.replaceChildren(...periods.map((p) => el("option", { value: p.period }, p.period + (p.locked ? " (locked)" : ""))));
    state.period = periods[periods.length - 1].period;
    periodSelect.value = state.period;
    await render();
  } catch (e) {
    main.replaceChildren(el("div", { class: "error", role: "alert" }, e.message));
  }
}

periodSelect.addEventListener("change", () => { state.period = periodSelect.value; render(); });

document.querySelectorAll(".tabs button").forEach((button) => {
  button.addEventListener("click", () => {
    state.tab = button.dataset.tab;
    document.querySelectorAll(".tabs button").forEach((b) => b.setAttribute("aria-selected", String(b === button)));
    render();
  });
});

runButton.addEventListener("click", async () => {
  runButton.disabled = true;
  try {
    await api("/api/close/runs", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ period: state.period }) });
    await render();
  } catch (e) {
    main.replaceChildren(el("div", { class: "error", role: "alert" }, e.message));
  } finally {
    runButton.disabled = false;
  }
});

exportButton.addEventListener("click", async () => {
  exportButton.disabled = true;
  try {
    const response = await api(`/api/export/pack.xlsx?period=${state.period}`);
    const url = URL.createObjectURL(await response.blob());
    const link = el("a", { href: url, download: `reporting-pack-${state.period}.xlsx` });
    document.body.append(link);
    link.click();
    link.remove();
    URL.revokeObjectURL(url);
  } catch (e) {
    main.replaceChildren(el("div", { class: "error", role: "alert" }, e.message));
  } finally {
    exportButton.disabled = false;
  }
});

keyForm.addEventListener("submit", (event) => {
  event.preventDefault();
  state.key = document.getElementById("apikey").value;
  try { sessionStorage.setItem("nc_key", state.key); } catch (e) { /* ignore */ }
  keyForm.hidden = true;
  init();
});

init();
