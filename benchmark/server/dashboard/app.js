const DATA_URL = "data/leaderboard.json";
const DEFAULT_LINKS = {
  paper: "https://example.com/repo-test-evolution-paper.pdf",
  github: "https://github.com/example/repo-test-evolution",
  dataset: "https://example.com/repo-test-evolution-dataset",
  submit: "mailto:benchmark@example.com",
};

const state = {
  data: null,
  rows: [],
  selectedId: null,
};

const elements = {
  body: document.getElementById("leaderboardBody"),
  search: document.getElementById("searchInput"),
  release: document.getElementById("releaseFilter"),
  mode: document.getElementById("modeFilter"),
  sort: document.getElementById("sortSelect"),
  notice: document.getElementById("dataNotice"),
  details: document.getElementById("submissionDetails"),
};

function asNumber(value) {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function formatPercent(value, digits = 1) {
  const number = asNumber(value);
  if (number === null) return "—";
  return `${(number * 100).toFixed(digits)}%`;
}

function formatNumber(value) {
  const number = asNumber(value);
  if (number === null) return "—";
  return number.toLocaleString("en-US");
}

function formatDate(value) {
  if (!value) return "not generated yet";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString("en-US", {
    year: "numeric",
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function metricCell(value, digits = 1) {
  const number = asNumber(value);
  const width = number === null ? 0 : Math.max(0, Math.min(100, number * 100));
  return `
    <div class="metric" aria-label="${formatPercent(number, digits)}">
      <span class="metric-value">${formatPercent(number, digits)}</span>
      <span class="metric-bar" aria-hidden="true"><span style="width:${width}%"></span></span>
    </div>`;
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function updateLinks(links = {}) {
  const merged = { ...DEFAULT_LINKS, ...links };
  const mapping = {
    paperLink: merged.paper,
    githubLink: merged.github,
    datasetLink: merged.dataset,
    submitLink: merged.submit,
    resourcePaper: merged.paper,
    resourceGithub: merged.github,
    resourceDataset: merged.dataset,
    resourceSubmission: merged.submit,
  };
  Object.entries(mapping).forEach(([id, href]) => {
    const link = document.getElementById(id);
    if (link && href) link.href = href;
  });
}

function summarize(data) {
  const rows = data.results || [];
  const releases = [...new Set(rows.map((row) => row.release_id).filter(Boolean))];
  const first = rows[0] || {};
  const counts = first.counts || {};
  document.getElementById("summaryRelease").textContent =
    releases.length === 1 ? releases[0] : releases.length ? `${releases.length} releases` : "—";
  document.getElementById("summaryGenerated").textContent = `Generated ${formatDate(data.generated_at)}`;
  document.getElementById("summarySubmissions").textContent = formatNumber(rows.length);
  document.getElementById("summaryEpisodes").textContent = formatNumber(counts.evaluated_count);
  document.getElementById("summaryPositive").textContent = formatNumber(counts.positive_count);
  document.getElementById("footerGenerated").textContent = `generated ${formatDate(data.generated_at)}`;
  document.getElementById("dataSourceLabel").textContent = data.source?.label || "server evaluation results";
}

function populateReleaseFilter(rows) {
  const current = elements.release.value;
  const releases = [...new Set(rows.map((row) => row.release_id).filter(Boolean))].sort();
  elements.release.innerHTML = '<option value="all">All releases</option>';
  releases.forEach((release) => {
    const option = document.createElement("option");
    option.value = release;
    option.textContent = release;
    elements.release.append(option);
  });
  if (releases.includes(current)) elements.release.value = current;
}

function getMetric(row, key) {
  return asNumber(row.metrics?.[key]);
}

function rowMatchesFilters(row) {
  const query = elements.search.value.trim().toLowerCase();
  const release = elements.release.value;
  const mode = elements.mode.value;
  const searchable = [
    row.submission_id,
    row.display_name,
    row.team,
    row.model,
    row.method,
    row.release_id,
  ]
    .filter(Boolean)
    .join(" ")
    .toLowerCase();
  if (query && !searchable.includes(query)) return false;
  if (release !== "all" && row.release_id !== release) return false;
  if (mode === "dynamic" && row.evaluation_status !== "dynamic") return false;
  if (mode === "static" && row.evaluation_status === "dynamic") return false;
  return true;
}

function sortedRows() {
  const key = elements.sort.value;
  return [...state.rows]
    .filter(rowMatchesFilters)
    .sort((left, right) => {
      const rightValue = getMetric(right, key) ?? -Infinity;
      const leftValue = getMetric(left, key) ?? -Infinity;
      if (rightValue !== leftValue) return rightValue - leftValue;
      return String(left.display_name || left.submission_id).localeCompare(
        String(right.display_name || right.submission_id),
      );
    });
}

function linkList(row) {
  const links = row.links || {};
  const items = [
    ["Paper", links.paper],
    ["Code", links.code || links.github],
    ["Report", links.report],
  ].filter(([, href]) => href);
  if (!items.length) return "—";
  return `<span class="link-list">${items
    .map(([label, href]) => `<a href="${escapeHtml(href)}">${escapeHtml(label)}</a>`)
    .join("")}</span>`;
}

function statusBadge(row) {
  const dynamic = row.evaluation_status === "dynamic";
  return `<span class="badge ${dynamic ? "dynamic" : "static"}">${dynamic ? "Dynamic" : "Static-only"}</span>`;
}

function renderTable() {
  const rows = sortedRows();
  if (!rows.length) {
    elements.body.innerHTML = `
      <tr class="empty-row"><td colspan="13">No submissions match the current filters.</td></tr>`;
    return;
  }
  elements.body.innerHTML = rows
    .map((row, index) => {
      const selected = row.submission_id === state.selectedId ? " selected" : "";
      return `
        <tr class="${selected}" data-submission-id="${escapeHtml(row.submission_id)}">
          <td>${index + 1}</td>
          <td>
            <div class="system-cell">
              <span class="system-name">${escapeHtml(row.display_name || row.submission_id)}</span>
              <span class="system-meta">${escapeHtml(row.team || "Anonymous")} · ${escapeHtml(row.model || "model unspecified")}</span>
            </div>
          </td>
          <td>${escapeHtml(row.release_id || "—")}</td>
          <td>${metricCell(row.metrics?.intent_macro_f1)}</td>
          <td>${metricCell(row.metrics?.positive_f1)}</td>
          <td>${metricCell(row.metrics?.positive_recall)}</td>
          <td>${metricCell(row.metrics?.accuracy)}</td>
          <td>${metricCell(row.metrics?.patch_apply_rate)}</td>
          <td>${metricCell(row.metrics?.csr)}</td>
          <td>${metricCell(row.metrics?.tps)}</td>
          <td>${metricCell(row.metrics?.end_to_end_success_rate)}</td>
          <td>${statusBadge(row)}</td>
          <td>${linkList(row)}</td>
        </tr>`;
    })
    .join("");
}

function detailTile(label, value) {
  return `<div class="metric-tile"><span>${escapeHtml(label)}</span><strong>${value}</strong></div>`;
}

function renderDetails(row) {
  if (!row) return;
  const metrics = row.metrics || {};
  const counts = row.counts || {};
  const artifacts = row.artifacts || {};
  elements.details.innerHTML = `
    <p class="eyebrow">Submission Detail</p>
    <h2>${escapeHtml(row.display_name || row.submission_id)}</h2>
    <p>${escapeHtml(row.method || "No method description provided yet.")}</p>
    <div class="metrics-grid">
      ${detailTile("Intent Macro-F1", formatPercent(metrics.intent_macro_f1))}
      ${detailTile("Positive F1", formatPercent(metrics.positive_f1))}
      ${detailTile("Patch Apply", formatPercent(metrics.patch_apply_rate))}
      ${detailTile("CSR", formatPercent(metrics.csr))}
      ${detailTile("TPS", formatPercent(metrics.tps))}
    </div>
    <dl class="detail-meta">
      <div><dt>Submission ID</dt><dd>${escapeHtml(row.submission_id)}</dd></div>
      <div><dt>Release</dt><dd>${escapeHtml(row.release_id || "—")}</dd></div>
      <div><dt>Evaluation Status</dt><dd>${row.evaluation_status === "dynamic" ? "Dynamic verified" : "Static only"}</dd></div>
      <div><dt>Validation Episodes</dt><dd>${formatNumber(counts.evaluated_count)}</dd></div>
      <div><dt>Positive Gold Cases</dt><dd>${formatNumber(counts.positive_count)}</dd></div>
      <div><dt>Intent TP Count</dt><dd>${formatNumber(counts.intent_tp_count)}</dd></div>
      <div><dt>Dynamic Attempts</dt><dd>${formatNumber(counts.dynamic_attempt_count)}</dd></div>
      <div><dt>Result Artifact</dt><dd>${escapeHtml(artifacts.result || "—")}</dd></div>
    </dl>`;
}

function showNotice(message) {
  elements.notice.textContent = message;
  elements.notice.classList.add("show");
}

function hideNotice() {
  elements.notice.textContent = "";
  elements.notice.classList.remove("show");
}

async function loadData() {
  try {
    const response = await fetch(DATA_URL, { cache: "no-store" });
    if (!response.ok) throw new Error(`${response.status} ${response.statusText}`);
    return await response.json();
  } catch (error) {
    showNotice(
      `Could not load ${DATA_URL}. Run the data builder script, then serve this folder over HTTP. Details: ${error.message}`,
    );
    return {
      schema_version: 1,
      generated_at: null,
      benchmark: { links: DEFAULT_LINKS },
      source: { label: "missing local data" },
      results: [],
    };
  }
}

function initEvents() {
  [elements.search, elements.release, elements.mode, elements.sort].forEach((control) => {
    control.addEventListener("input", renderTable);
    control.addEventListener("change", renderTable);
  });
  elements.body.addEventListener("click", (event) => {
    const rowElement = event.target.closest("tr[data-submission-id]");
    if (!rowElement) return;
    state.selectedId = rowElement.dataset.submissionId;
    renderTable();
    renderDetails(state.rows.find((row) => row.submission_id === state.selectedId));
  });
}

async function init() {
  const data = await loadData();
  state.data = data;
  state.rows = data.results || [];
  updateLinks(data.benchmark?.links);
  summarize(data);
  populateReleaseFilter(state.rows);
  renderTable();
  if (state.rows.length) {
    state.selectedId = sortedRows()[0]?.submission_id || state.rows[0].submission_id;
    renderTable();
    renderDetails(state.rows.find((row) => row.submission_id === state.selectedId));
    hideNotice();
  }
}

initEvents();
init();
