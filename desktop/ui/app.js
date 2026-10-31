/* Codebase Search UI - framework-free on purpose (3 pages; see docs/planning §36). All dynamic text is inserted with
   textContent / text nodes, never innerHTML: indexed source code is untrusted input. */
"use strict";
const TOKEN = document.querySelector('meta[name="slcs-token"]').content;
const state = {
  repos: [], health: null,
  search: { query: "", mode: "auto", languages: new Set(), limit: 10, scope: "", response: null, error: null, busy: false },
};

// ---------------------------------------------------------------------------------------------- helpers
async function api(path, { method = "GET", body } = {}) {
  const res = await fetch("/api" + path, {
    method, headers: { "X-SLCS-Token": TOKEN, ...(body ? { "Content-Type": "application/json" } : {}) },
    body: body ? JSON.stringify(body) : undefined,
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw Object.assign(new Error((data.error && data.error.message) || res.statusText), { code: data.error && data.error.code, status: res.status });
  return data;
}
function h(tag, attrs, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === false || v == null) continue;
    if (k === "class") el.className = v;
    else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else el.setAttribute(k, v === true ? "" : v);
  }
  for (const kid of kids.flat()) { if (kid == null || kid === false) continue; el.append(kid.nodeType ? kid : document.createTextNode(String(kid))); }
  return el;
}
const $ = (s) => document.querySelector(s);
let toastTimer;
function toast(msg, isErr) {
  const t = $("#toast"); t.textContent = msg; t.className = "toast show" + (isErr ? " err" : "");
  clearTimeout(toastTimer); toastTimer = setTimeout(() => (t.className = "toast"), 3800);
}
function confirmDialog(title, body, okLabel) {
  const d = $("#confirm"); $("#confirm-title").textContent = title; $("#confirm-body").textContent = body; $("#confirm-ok").textContent = okLabel || "Confirm";
  return new Promise((resolve) => { d.addEventListener("close", () => resolve(d.returnValue === "ok"), { once: true }); d.showModal(); });
}
const ago = (iso) => { if (!iso) return "never"; const s = (Date.now() - Date.parse(iso)) / 1000; return s < 60 ? "just now" : s < 3600 ? Math.round(s / 60) + " min ago" : s < 86400 ? Math.round(s / 3600) + " h ago" : Math.round(s / 86400) + " d ago"; };
const STATUS_LABEL = { registered: "Not indexed yet", indexing: "Indexing…", indexed: "Indexed", indexed_with_errors: "Indexed with errors", failed: "Failed", unavailable: "Folder missing", needs_reindex: "Needs re-index", interrupted: "Interrupted – resume", cancelled: "Cancelled", stale: "Moved – re-index" };
const esc = (s) => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");

// ---------------------------------------------------------------------------------------------- shared data
async function refreshRepos() { state.repos = (await api("/repositories")).repositories; renderSide(); }
async function refreshHealth() {
  try { state.health = await api("/health"); } catch (e) { state.health = null; }
  const pill = $("#model-pill"); const m = state.health && state.health.model;
  if (!m) { pill.textContent = "server unreachable"; pill.className = "pill bad"; return; }
  const ok = m.available && m.compatible;
  pill.textContent = (m.provider === "hash" ? "Offline baseline embedder" : m.model) + (ok ? " · ready" : m.compatible === false ? " · re-index needed" : " · unavailable");
  pill.className = "pill " + (ok ? "ok" : "bad");
}
function renderSide() {
  const ul = $("#side-repos"); ul.replaceChildren();
  if (!state.repos.length) ul.append(h("li", { class: "none" }, "None yet"));
  for (const r of state.repos) ul.append(h("li", {}, h("span", { class: "dot " + r.status, "aria-hidden": "true" }), h("a", { href: "#/repos/" + r.id, class: "grow", style: null }, r.name), h("span", { class: "sr-only" }, STATUS_LABEL[r.status] || r.status)));
}

// ---------------------------------------------------------------------------------------------- search page
function highlighter(resp) {
  const terms = new Set((resp.highlight || []).filter((t) => t.length >= 3));
  if (resp.query_class !== "natural") terms.add(resp.query.replace(/^["'`]|["'`]$/g, "").replace(/\(\)$/, ""));
  if (!terms.size) return (t) => [t];
  const rx = new RegExp("(" + [...terms].sort((a, b) => b.length - a.length).map(esc).join("|") + ")", "gi");
  return (text) => text.split(rx).map((part, i) => (i % 2 ? h("mark", {}, part) : part));
}
function pip(label, on) { return h("span", { class: "pip" + (on ? " on" : "") }, h("i", { "aria-hidden": "true" }), label); }
function resultCard(r, hl) {
  const s = r.scores, lines = r.snippet.split("\n"), clamp = lines.length > 12;
  const code = h("div", { class: "code" + (clamp ? " clamped" : ""), role: "region", "aria-label": "Source lines " + r.start_line + " to " + r.end_line },
    lines.map((ln, i) => h("div", { class: "line" }, h("span", { class: "no" }, r.start_line + i), h("span", {}, ...hl(ln || " ")))));
  const toggle = clamp ? h("button", { class: "btn small", "aria-expanded": "false", onclick: (e) => { const open = code.classList.toggle("clamped") === false; e.target.textContent = open ? "Collapse" : "Show all " + lines.length + " lines"; e.target.setAttribute("aria-expanded", String(open)); } }, "Show all " + lines.length + " lines") : null;
  const evidence = [s.symbol_tier != null || s.literal_match ? "symbol/exact text" : null, s.lexical_rank ? "keyword" : null, s.semantic_rank ? "meaning" : null].filter(Boolean);
  return h("article", { class: "result", "aria-label": (r.symbol_name || r.relative_path) },
    h("div", { class: "result-head" },
      h("div", { class: "result-title" }, h("span", { class: "sym" }, r.symbol_name || "(file section)"), h("span", { class: "kind" }, r.symbol_kind + " · " + r.language),
        h("span", { class: "badges" },
          h("span", { class: "badge " + r.confidence, title: "Agreement between independent search signals. Not a probability of correctness." }, r.confidence + " agreement"),
          h("span", { class: "badge " + r.freshness }, r.freshness === "fresh" ? "up to date" : r.freshness === "stale" ? "file changed – re-index" : "file missing"))),
      h("div", { class: "loc" }, r.repository_name + " / ", h("b", {}, r.relative_path), " : " + r.start_line + "–" + r.end_line),
      h("div", { class: "rail", role: "group", "aria-label": "Evidence: " + (evidence.join(", ") || "none") },
        pip("Symbol / exact", s.symbol_tier != null || s.literal_match), pip("Keyword", !!s.lexical_rank), pip("Meaning", !!s.semantic_rank)),
      h("ul", { class: "reasons" }, r.reasons.map((x) => h("li", {}, x)))),
    code,
    h("div", { class: "result-actions" },
      h("button", { class: "btn primary small", onclick: () => openResult(r) }, "Open in editor"),
      h("button", { class: "btn small", onclick: () => copy(r.relative_path + ":" + r.start_line, "Path copied") }, "Copy path"),
      h("button", { class: "btn small", onclick: () => copy(r.snippet, "Snippet copied") }, "Copy snippet"), toggle,
      r.duplicates ? h("span", { class: "dup" }, "+" + r.duplicates + " identical copy elsewhere") : null));
}
async function copy(text, msg) { try { await navigator.clipboard.writeText(text); toast(msg); } catch (e) { toast("Clipboard unavailable in this window", true); } }
async function openResult(r) {
  try { const out = await api("/open", { method: "POST", body: { chunk_id: r.chunk_id } }); toast("Opened " + out.relative_path + ":" + out.line + " in " + out.editor); }
  catch (e) { toast(e.message, true); }
}
async function runSearch() {
  const S = state.search; if (!S.query.trim()) { S.error = { message: "Enter a question or a symbol name to search for." }; S.response = null; return renderSearchResults(); }
  S.busy = true; S.error = null; renderSearchResults();
  try {
    S.response = await api("/search", { method: "POST", body: { query: S.query, mode: S.mode === "symbol" ? "symbol" : "auto", repository_ids: S.scope ? [S.scope] : null, languages: S.languages.size ? [...S.languages] : null, limit: S.limit } });
  } catch (e) { S.response = null; S.error = { message: e.message, code: e.code }; }
  S.busy = false; renderSearchResults();
  if (S.response && S.response.warnings.length) refreshHealth();        // keep the sidebar model status truthful
}
function renderSearchResults() {
  const box = $("#results"); if (!box) return; box.replaceChildren();
  const S = state.search;
  if (S.busy) { box.append(h("p", { class: "muted", role: "status" }, "Searching…")); return; }
  if (S.error) { box.append(h("div", { class: "banner err", role: "alert" }, S.error.message, S.error.code === "model_mismatch" ? [" ", h("a", { href: "#/settings" }, "Open settings")] : null)); return; }
  const R = S.response;
  if (!R) { box.append(h("div", { class: "empty" }, h("strong", {}, "Ask about your code"), "Try “where are API errors logged?” or look up a name such as refreshAccessToken.")); return; }
  for (const w of R.warnings) box.append(h("div", { class: "banner warn", role: "alert" }, w));
  if (R.message) box.append(h("div", { class: "banner " + (R.results.length ? "warn" : "info"), role: "status" }, R.message));
  if (R.results.length) {
    const t = R.timings_ms.total_ms;
    box.append(h("p", { class: "meta", role: "status" }, R.results.length + " results · " + R.query_class + " query · " + t + " ms · nothing left this device"));
    const hl = highlighter(R); for (const r of R.results) box.append(resultCard(r, hl));
  }
}
function pageSearch(main) {
  const S = state.search;
  const langs = [...new Set(state.repos.flatMap((r) => Object.keys(r.stats.languages || {})))].sort();
  const input = h("input", { id: "q", type: "text", value: S.query, placeholder: S.mode === "symbol" ? "Symbol, route, env var, error code…" : "Ask in words, e.g. where do we handle expired tokens?", autocomplete: "off", spellcheck: "false", "aria-label": "Search query" });
  input.addEventListener("input", () => (S.query = input.value));
  const form = h("form", { class: "searchbar", role: "search", onsubmit: (e) => { e.preventDefault(); runSearch(); } }, input, h("button", { class: "btn primary", type: "submit" }, "Search"));
  const seg = h("div", { class: "seg", role: "group", "aria-label": "Search mode" }, [["auto", "Ask in words"], ["symbol", "Exact symbol"]].map(([m, l]) => h("button", { type: "button", "aria-pressed": String(S.mode === m), onclick: () => { S.mode = m; pageSearch(main); if (S.query) runSearch(); } }, l)));
  const scope = h("select", { "aria-label": "Repository scope", onchange: (e) => (S.scope = e.target.value) }, h("option", { value: "" }, "All repositories"), state.repos.map((r) => h("option", { value: r.id, selected: S.scope === r.id }, r.name)));
  const chips = h("div", { class: "chips", role: "group", "aria-label": "Language filter" }, langs.map((l) => h("button", { type: "button", class: "chip", "aria-pressed": String(S.languages.has(l)), onclick: (e) => { S.languages.has(l) ? S.languages.delete(l) : S.languages.add(l); e.target.setAttribute("aria-pressed", String(S.languages.has(l))); if (S.query) runSearch(); } }, l)));
  const limit = h("select", { "aria-label": "Maximum results", onchange: (e) => (S.limit = +e.target.value) }, [5, 10, 20, 50].map((n) => h("option", { value: n, selected: S.limit === n }, "Show " + n)));
  main.replaceChildren(h("div", { class: "page-head" }, h("h1", {}, "Search"), h("p", { class: "muted" }, "Find where something is implemented. Results always show the real source lines.")),
    form, h("div", { class: "filters" }, seg, h("div", {}, scope), langs.length ? h("div", { class: "row" }, h("span", { class: "lbl" }, "Language"), chips) : null, limit),
    h("div", { id: "results", "aria-live": "polite" }));
  renderSearchResults();
}

// ---------------------------------------------------------------------------------------------- repositories
function langBar(langs) {
  const total = Object.values(langs).reduce((a, b) => a + b, 0) || 1, ent = Object.entries(langs);
  const bar = h("div", { class: "langbar", role: "img", "aria-label": "Languages: " + ent.map(([k, v]) => k + " " + v).join(", ") });
  ent.forEach(([, v], i) => { const s = h("span", { class: "l" + (i % 10) }); s.style.width = (v / total) * 100 + "%"; bar.append(s); });
  return h("div", {}, bar, h("div", { class: "legend" }, ent.map(([k, v], i) => h("span", {}, h("i", { class: "l" + (i % 10) }), k + " " + v))));
}
function repoActions(r, detail) {
  const busy = r.running || r.status === "indexing";
  return h("div", { class: "row" },
    busy ? h("button", { class: "btn small", onclick: async () => { await api("/repositories/" + r.id + "/cancel", { method: "POST" }); toast("Cancelling…"); } }, "Cancel")
      : [h("button", { class: "btn primary small", disabled: !r.available, title: r.available ? null : "Folder is missing", onclick: () => startIndex(r, "incremental") }, r.stats.chunks ? "Update index" : "Index now"),
         h("button", { class: "btn small", disabled: !r.available, title: r.available ? null : "Folder is missing", onclick: () => startIndex(r, "full") }, "Full re-index")],
    detail ? null : h("a", { class: "btn small", href: "#/repos/" + r.id }, "Details"),
    h("button", { class: "btn danger small", disabled: busy, onclick: () => removeRepo(r) }, "Remove"));
}
async function startIndex(r, mode) { try { await api("/repositories/" + r.id + "/index", { method: "POST", body: { mode } }); toast("Indexing started"); await refreshRepos(); route(); } catch (e) { toast(e.message, true); } }
async function removeRepo(r) {
  if (!(await confirmDialog("Remove " + r.name + "?", "This deletes the search index for this repository. Your source files are not touched.", "Remove index"))) return;
  try { await api("/repositories/" + r.id, { method: "DELETE" }); toast("Removed " + r.name); await refreshRepos(); location.hash = "#/repos"; route(); } catch (e) { toast(e.message, true); }
}
function repoCard(r) {
  const j = r.job; const pct = j && j.files_seen ? Math.min(100, Math.round(((j.files_indexed + j.files_unchanged + j.files_failed) / j.files_seen) * 100)) : 0;
  const prog = h("div", { class: "progress", role: "progressbar", "aria-valuemin": "0", "aria-valuemax": "100", "aria-valuenow": String(pct), "aria-label": "Indexing progress" }, h("span", {}));
  prog.firstChild.style.width = pct + "%";
  return h("section", { class: "card", "aria-label": r.name },
    h("div", { class: "row between" }, h("div", {}, h("h2", {}, r.name), h("div", { class: "loc" }, r.root_path)),
      h("div", { class: "row" }, h("span", { class: "dot " + r.status, "aria-hidden": "true" }), h("strong", {}, STATUS_LABEL[r.status] || r.status))),
    r.running ? prog : null,
    h("div", { class: "stats" }, [["files", r.stats.files], ["chunks", r.stats.chunks], ["skipped", r.stats.skipped], ["file errors", r.stats.file_errors]].map(([k, v]) => h("div", { class: "stat" }, h("b", {}, v), h("span", {}, k)))),
    Object.keys(r.stats.languages || {}).length ? langBar(r.stats.languages) : null,
    h("p", { class: "meta" }, "Last indexed " + ago(r.last_indexed_at) + (r.last_error ? " · last error: " + r.last_error : "")),
    r.status === "unavailable" ? h("div", { class: "banner err", role: "alert" }, "The folder is missing or inaccessible. Your existing index is preserved; restore the folder to continue.") : null,
    repoActions(r, false));
}
function pageRepos(main) {
  const path = h("input", { id: "new-path", type: "text", placeholder: "/home/me/projects/my-app   or   C:\\Projects\\my-app", "aria-label": "Repository folder path", autocomplete: "off" });
  const name = h("input", { type: "text", placeholder: "Display name (optional)", "aria-label": "Display name" });
  const add = async (e) => { e.preventDefault(); try { const out = await api("/repositories", { method: "POST", body: { path: path.value.trim(), name: name.value.trim() || null } }); toast("Added " + out.repository.name); path.value = name.value = ""; await refreshRepos(); await startIndex(out.repository, "incremental"); } catch (err) { toast(err.message, true); } };
  main.replaceChildren(h("div", { class: "page-head" }, h("h1", {}, "Repositories"), h("p", { class: "muted" }, "Only folders you add here are ever read. Symlinks and secret files (.env, keys) are skipped.")),
    h("form", { class: "card", onsubmit: add }, h("div", { class: "form-grid" }, h("div", {}, h("label", { for: "new-path" }, "Repository folder"), path), h("div", {}, h("label", {}, "Name"), name)), h("div", { class: "row end", }, h("button", { class: "btn primary", type: "submit" }, "Add and index"))),
    ...(state.repos.length ? state.repos.map(repoCard) : [h("div", { class: "empty" }, h("strong", {}, "No repositories yet"), "Add a project folder above to start.")]));
}
async function pageRepoDetail(main, id) {
  let r; try { r = (await api("/repositories/" + id)).repository; } catch (e) { main.replaceChildren(h("div", { class: "banner err" }, e.message)); return; }
  const groups = {}; for (const s of r.skipped) (groups[s.reason.split(" ")[0]] ||= []).push(s.rel_path);
  const REASON = { sensitive: "Secret-like files (never indexed)", excluded_dir: "Dependency / build folders", gitignored: "Ignored by .gitignore", generated: "Generated code", minified: "Minified", symlink: "Symbolic links (never followed)", too_large: "Over the size limit", lockfile_or_minified: "Lockfiles / minified", binary: "Binary", empty: "Empty files", user_ignore: "Your ignore patterns", file_limit: "Over the file-count limit" };
  main.replaceChildren(h("p", {}, h("a", { href: "#/repos" }, "← Repositories")), repoCard(r),
    h("section", { class: "card" }, h("h2", {}, "What was skipped, and why"), Object.keys(groups).length ? Object.entries(groups).map(([k, v]) => h("details", {}, h("summary", {}, (REASON[k] || k) + " (" + v.length + ")"), h("p", { class: "loc" }, v.join("   ")))) : h("p", { class: "muted" }, "Nothing skipped.")),
    r.file_errors.length ? h("section", { class: "card" }, h("h2", {}, "Files that failed to index"), h("p", { class: "muted" }, "Previous index data for these files is kept. They are retried on the next update."), h("table", {}, h("thead", {}, h("tr", {}, h("th", {}, "File"), h("th", {}, "Error"))), h("tbody", {}, r.file_errors.map((f) => h("tr", {}, h("td", { class: "mono" }, f.rel_path), h("td", {}, f.error)))))) : null,
    r.warnings.length ? h("section", { class: "card" }, h("h2", {}, "Parser notes"), h("p", { class: "muted" }, "These files were indexed with a fallback strategy (line windows)."), h("table", {}, h("tbody", {}, r.warnings.map((f) => h("tr", {}, h("td", { class: "mono" }, f.rel_path), h("td", {}, f.warning))))) ) : null);
}

// ---------------------------------------------------------------------------------------------- settings
async function pageSettings(main) {
  const [s, m] = await Promise.all([api("/settings"), api("/model")]);
  const f = {}; const field = (k, label, el, help) => { f[k] = el; return h("div", {}, h("label", { for: "s-" + k }, label), el, help ? h("div", { class: "help" }, help) : null); };
  const input = (k, type, v) => h("input", { id: "s-" + k, type, value: v });
  const sel = (k, opts, v) => h("select", { id: "s-" + k }, opts.map(([val, lab]) => h("option", { value: val, selected: val === v }, lab)));
  const chk = h("input", { id: "s-respect_gitignore", type: "checkbox" }); chk.checked = !!s.respect_gitignore; f.respect_gitignore = chk;
  const ignore = h("textarea", { id: "s-extra_ignore", "aria-label": "Extra ignore patterns" }); ignore.value = (s.extra_ignore || []).join("\n"); f.extra_ignore = ignore;
  const save = async (e) => {
    e.preventDefault();
    try {
      const patch = { embedding_provider: f.embedding_provider.value, ollama_url: f.ollama_url.value, ollama_model: f.ollama_model.value, editor: f.editor.value, result_limit: +f.result_limit.value, max_file_bytes: +f.max_file_bytes.value, respect_gitignore: f.respect_gitignore.checked, extra_ignore: f.extra_ignore.value.split("\n").map((x) => x.trim()).filter(Boolean), log_level: f.log_level.value };
      await api("/settings", { method: "PUT", body: patch }); toast("Settings saved"); await refreshHealth(); pageSettings(main);
    } catch (err) { toast(err.message, true); }
  };
  const reindexAll = async () => {
    if (!(await confirmDialog("Re-index everything for the new model?", "Existing vectors are deleted and rebuilt. Source files are not touched. This can take a while on large repositories.", "Delete vectors and rebuild"))) return;
    await api("/model/reindex-all", { method: "POST" }); await refreshRepos(); for (const r of state.repos) { try { await api("/repositories/" + r.id + "/index", { method: "POST", body: { mode: "full" } }); } catch (e) { /* surfaced on repo page */ } }
    location.hash = "#/repos"; toast("Re-indexing started");
  };
  main.replaceChildren(h("div", { class: "page-head" }, h("h1", {}, "Settings"), h("p", { class: "muted" }, "Everything here is stored on this device.")),
    h("section", { class: "card", "aria-label": "Embedding model" }, h("h2", {}, "Embedding model"),
      h("p", {}, h("strong", {}, m.provider === "hash" ? "Offline baseline embedder" : m.model), " — ", m.available ? "available" : (m.error || "unavailable")),
      h("p", { class: "muted" }, m.provider === "hash" ? "A deterministic offline baseline, not a neural model. For better semantic matching install Ollama and an embedding model, then choose “ollama” below." : "Requests go to " + (m.endpoint || "your local Ollama") + "."),
      m.compatible ? null : h("div", { class: "banner warn", role: "alert" }, "The index was built with “" + m.index_model + "”. Vectors from different models cannot be mixed. ", h("button", { class: "btn small", onclick: reindexAll }, "Re-index all repositories"))),
    h("form", { class: "card", onsubmit: save, "aria-label": "Settings form" }, h("div", { class: "form-grid" },
      field("embedding_provider", "Embedding provider", sel("embedding_provider", [["hash", "hash (offline baseline)"], ["ollama", "ollama (local neural model)"]], s.embedding_provider)),
      field("ollama_model", "Ollama model", input("ollama_model", "text", s.ollama_model), "Example: nomic-embed-text (run: ollama pull nomic-embed-text)"),
      field("ollama_url", "Ollama URL", input("ollama_url", "text", s.ollama_url), "Must be this machine (127.0.0.1 / localhost). Remote endpoints are blocked."),
      field("editor", "Preferred editor", sel("editor", ["vscode", "cursor", "vscodium", "sublime", "idea", "pycharm", "notepadpp"].map((x) => [x, x]), s.editor), "Opened with its command-line launcher, never through a shell."),
      field("result_limit", "Results per search", input("result_limit", "number", s.result_limit)),
      field("max_file_bytes", "Largest file to index (bytes)", input("max_file_bytes", "number", s.max_file_bytes)),
      field("log_level", "Log level", sel("log_level", ["DEBUG", "INFO", "WARNING", "ERROR"].map((x) => [x, x]), s.log_level), "Logs never contain source code or queries."),
      h("div", {}, h("label", { for: "s-respect_gitignore" }, "Respect .gitignore"), h("div", { class: "row" }, chk, h("span", {}, "Skip files that git ignores"))),
      h("div", { class: "full" }, field("extra_ignore", "Extra ignore patterns (one per line)", ignore)),
      h("div", { class: "full row end" }, h("button", { class: "btn primary", type: "submit" }, "Save settings")))),
    h("section", { class: "card", "aria-label": "Privacy" }, h("h2", {}, "Privacy"), h("table", {}, h("tbody", {},
      [["Telemetry", "None. There is no setting to turn on."], ["Network", "Only the local embedding endpoint, and only if it is on this machine."], ["Stored data", "Index database and settings in " + s.data_dir], ["Deleting data", "Removing a repository deletes its index. Source files are never modified."]].map(([a, b]) => h("tr", {}, h("th", {}, a), h("td", {}, b)))))));
}

// ---------------------------------------------------------------------------------------------- router
let poll;
async function route() {
  const main = $("#main"); const hash = location.hash || "#/search"; const [, page, arg] = hash.split("/");
  document.querySelectorAll("#nav a").forEach((a) => (a.dataset.page === page ? a.setAttribute("aria-current", "page") : a.removeAttribute("aria-current")));
  clearInterval(poll); refreshHealth();
  try {
    await refreshRepos();
    if (page === "search") pageSearch(main);
    else if (page === "repos" && arg) await pageRepoDetail(main, arg);
    else if (page === "repos") pageRepos(main);
    else if (page === "settings") await pageSettings(main);
    else pageSearch(main);
  } catch (e) { main.replaceChildren(h("div", { class: "banner err", role: "alert" }, e.message)); }
  if (state.repos.some((r) => r.running)) poll = setInterval(async () => { await refreshRepos(); if (!state.repos.some((r) => r.running)) { clearInterval(poll); } if (location.hash.startsWith("#/repos")) { const keep = document.activeElement; if (!keep || keep.tagName === "BODY") route(); } }, 1200);
}
window.addEventListener("hashchange", route);
document.addEventListener("keydown", (e) => { if ((e.key === "/" || (e.key === "k" && (e.ctrlKey || e.metaKey))) && !/INPUT|TEXTAREA|SELECT/.test(document.activeElement.tagName)) { const q = $("#q"); if (q) { e.preventDefault(); q.focus(); q.select(); } } });
refreshHealth().then(route);
