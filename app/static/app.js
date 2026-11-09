const $ = (s) => document.querySelector(s);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const api = async (path, opts) => {
  const r = await fetch("/api" + path, opts);
  if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || r.statusText);
  return r.json();
};
const store = {
  get: (k, d) => { try { return JSON.parse(localStorage.getItem(k)) ?? d; } catch { return d; } },
  set: (k, v) => { try { localStorage.setItem(k, JSON.stringify(v)); } catch {} },
};

const PRIMARY_ORDER = ["Album", "EP", "Single", "Broadcast", "Other"];
const SECONDARY = ["Studio", "Compilation", "Soundtrack", "Spokenword", "Interview", "Audiobook", "Audio drama",
  "Live", "Remix", "DJ-mix", "Mixtape/Street", "Demo", "Field recording"];

let state = { artists: [], filter: store.get("filter", "all"), sort: store.get("sort", "name"),
  q: "", size: store.get("size", 170), hideComplete: store.get("hideComplete", false) };

function bar(owned, total, label) {
  if (total == null) return `<div class="bar none"><span>${owned} / ?</span></div>`;
  const pct = total ? Math.round((100 * owned) / total) : 0;
  const cls = pct >= 100 ? "" : pct > 0 ? "partial" : "none";
  return `<div class="bar ${cls}"><i style="width:${pct}%"></i><span>${label ?? `${owned} / ${total}`}</span></div>`;
}
const year = (d) => (d || "").slice(0, 4) || "—";
const fmtTime = (ms) => { if (!ms) return ""; const s = Math.round(ms / 1000); return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`; };

/* ---------- status / jobs ---------- */
let pollTimer;
async function pollStatus() {
  clearTimeout(pollTimer);
  let s;
  try { s = await api("/status"); } catch { pollTimer = setTimeout(pollStatus, 5000); return; }
  const j = s.job;
  const busy = j.running;
  const pct = j.total ? Math.round((100 * j.done) / j.total) : 0;
  $("#status").innerHTML = `
    <div>${s.artists} artists · ${s.files} files${s.unmatched ? ` · ${s.unmatched} untagged` : ""}</div>
    ${busy ? `<div class="bar"><i style="width:${pct}%"></i><span>${pct}%</span></div><div>${esc(j.phase)}: ${esc(j.message)}</div>` : ""}
    ${j.error ? `<div class="miss">${esc(j.error)}</div>` : ""}`;
  const btn = $("#btn-scan");
  if (btn) btn.disabled = busy;
  if (state.wasBusy && !busy) route();       // refresh when a job finishes
  else if (busy && state.view === "library" && Date.now() - (state.lastRender || 0) > 8000) route(true);
  state.wasBusy = busy;
  pollTimer = setTimeout(pollStatus, busy ? 2000 : 6000);
}

async function startScan(full) {
  await api("/scan?full=" + !!full, { method: "POST" });
  pollStatus();
}

/* ---------- library ---------- */
async function showLibrary(soft) {
  state.view = "library";
  document.querySelectorAll("nav a").forEach((a) => a.classList.toggle("active", a.dataset.nav === "library"));
  $("#toolbar").innerHTML = `
    <button id="btn-scan" class="primary">⟳ Scan library</button>
    <button id="btn-full" title="Re-fetch everything from MusicBrainz">Refresh all metadata</button>
    <input id="q" type="search" placeholder="Filter artists…" value="${esc(state.q)}">
    <span class="spacer"></span>
    <select id="filter">
      <option value="all">All artists</option><option value="incomplete">Incomplete</option>
      <option value="complete">Complete</option><option value="unsynced">Not synced yet</option>
    </select>
    <select id="sort">
      <option value="name">Sort: Name</option><option value="percent">Sort: Completion</option>
      <option value="missing">Sort: Most missing</option><option value="albums">Sort: Albums</option>
    </select>
    <input id="size" type="range" min="110" max="280" value="${state.size}" title="Poster size">`;
  $("#filter").value = state.filter; $("#sort").value = state.sort;
  $("#btn-scan").onclick = () => startScan(false);
  $("#btn-full").onclick = () => confirm("Re-fetch all artists from MusicBrainz? This takes a while (1 request/second).") && startScan(true);
  $("#q").oninput = (e) => { state.q = e.target.value; renderGrid(); };
  $("#filter").onchange = (e) => { state.filter = e.target.value; store.set("filter", state.filter); renderGrid(); };
  $("#sort").onchange = (e) => { state.sort = e.target.value; store.set("sort", state.sort); renderGrid(); };
  $("#size").oninput = (e) => { state.size = e.target.value; store.set("size", +state.size); renderGrid(); };
  state.artists = await api("/artists");
  state.lastRender = Date.now();
  if (!soft) $("#view").innerHTML = "";
  renderGrid();
}

function renderGrid() {
  document.documentElement.style.setProperty("--size", state.size + "px");
  const q = state.q.toLowerCase();
  let list = state.artists.filter((a) => (a.name + " " + (a.sort_name || "")).toLowerCase().includes(q));
  if (state.filter === "incomplete") list = list.filter((a) => a.percent !== null && a.percent < 100);
  if (state.filter === "complete") list = list.filter((a) => a.percent === 100);
  if (state.filter === "unsynced") list = list.filter((a) => !a.synced);
  const missing = (a) => a.tracks_total - a.tracks_owned;
  const sorters = {
    name: (a, b) => (a.sort_name || a.name).localeCompare(b.sort_name || b.name, undefined, { sensitivity: "base" }),
    percent: (a, b) => (a.percent ?? -1) - (b.percent ?? -1),
    missing: (a, b) => missing(b) - missing(a),
    albums: (a, b) => b.albums_total - a.albums_total,
  };
  list.sort(sorters[state.sort]);
  if (!list.length) {
    $("#view").innerHTML = `<div class="empty">${state.artists.length ? "No artists match." :
      "No tagged music found yet. Start a scan, or check that your music folder is mounted at /music."}</div>`;
    return;
  }
  $("#view").innerHTML = `<div class="grid">${list.map((a) => `
    <a class="card" href="#/artist/${a.mbid}">
      <div class="cover" style="background-image:url(/api/cover/artist/${a.mbid})">🎤
        ${a.synced ? "" : '<span class="badge">not synced</span>'}</div>
      <div class="meta"><div class="title" title="${esc(a.name)}">${esc(a.name)}</div>
        <div class="sub">${a.albums_owned} / ${a.albums_total} albums</div></div>
      ${a.tracks_total ? bar(a.tracks_owned, a.tracks_total, `${a.tracks_owned} / ${a.tracks_total} · ${a.percent}%`) : bar(0, null)}
    </a>`).join("")}</div>`;
}

/* ---------- artist ---------- */
async function showArtist(mbid) {
  state.view = "artist";
  document.querySelectorAll("nav a").forEach((a) => a.classList.remove("active"));
  let a;
  try { a = await api("/artists/" + mbid); } catch (e) { $("#view").innerHTML = `<div class="empty">${esc(e.message)}</div>`; return; }
  const wantedOnly = state.showAll ? a.albums : a.albums.filter((x) => x.wanted);
  $("#toolbar").innerHTML = `
    <a href="#/"><button>← Library</button></a>
    <button id="btn-refresh">⟳ Refresh from MusicBrainz</button>
    <span class="spacer"></span>
    <label class="chk"><input type="checkbox" id="hide-complete" ${state.hideComplete ? "checked" : ""}> Hide complete</label>
    <label class="chk"><input type="checkbox" id="show-all" ${state.showAll ? "checked" : ""}> Show all types</label>
    <a href="https://musicbrainz.org/artist/${a.mbid}" target="_blank" rel="noopener"><button>MusicBrainz ↗</button></a>`;
  $("#btn-refresh").onclick = async () => { await api(`/artists/${mbid}/refresh`, { method: "POST" }); pollStatus(); };
  $("#hide-complete").onchange = (e) => { state.hideComplete = e.target.checked; store.set("hideComplete", state.hideComplete); showArtist(mbid); };
  $("#show-all").onchange = (e) => { state.showAll = e.target.checked; showArtist(mbid); };

  const cover = `/api/cover/artist/${a.mbid}`;
  const meta = [a.type, a.country, a.disambiguation].filter(Boolean).map(esc).join(" · ");
  let html = `<div class="hero"><div class="bg" style="background-image:url(${cover})"></div><div class="row">
    <div class="poster" style="background-image:url(${cover})"></div>
    <div><h1>${esc(a.name)}</h1><div class="small">${meta}</div>
      <div class="facts"><div><b>${a.albums_owned} / ${a.albums_total}</b>albums</div>
        <div><b>${a.tracks_owned} / ${a.tracks_total}</b>tracks</div>
        <div><b>${a.percent ?? "—"}${a.percent != null ? "%" : ""}</b>complete</div></div>
      ${a.tracks_total ? bar(a.tracks_owned, a.tracks_total, `${a.percent}%`) : ""}
      ${a.synced ? "" : '<p class="small">Not synced with MusicBrainz yet — a sync is queued.</p>'}
    </div></div></div>`;

  const groups = {};
  for (const x of wantedOnly) {
    if (state.hideComplete && x.total && x.owned >= x.total) continue;
    const key = PRIMARY_ORDER.includes(x.primary_type) ? x.primary_type : "Other";
    (groups[key] ||= []).push(x);
  }
  for (const type of PRIMARY_ORDER) {
    const items = groups[type];
    if (!items) continue;
    const have = items.filter((x) => x.in_library).length;
    html += `<h2 class="section">${type}s<small>${have} / ${items.length}</small></h2><div class="grid">` +
      items.map((x) => `
        <div class="card ${x.wanted && !x.in_library ? "" : x.in_library ? "" : "dim"}" data-rg="${x.mbid}">
          <div class="cover" style="background-image:url(/api/cover/album/${x.mbid})">💿
            ${x.secondary_types.length ? `<span class="badge">${esc(x.secondary_types.join(", "))}</span>` : ""}</div>
          <div class="meta"><div class="title" title="${esc(x.title)}">${esc(x.title)}</div>
            <div class="sub">${year(x.date)}</div></div>
          ${bar(x.owned, x.total)}
        </div>`).join("") + "</div>";
  }
  if (!Object.keys(groups).length) html += '<div class="empty">Nothing to show (check your type filters in Settings).</div>';
  $("#view").innerHTML = html;
  document.querySelectorAll("[data-rg]").forEach((el) => (el.onclick = () => showAlbum(mbid, el.dataset.rg)));
}

/* ---------- album modal ---------- */
async function showAlbum(artist, rg) {
  openModal('<div class="empty">Loading track list…</div>');
  let al;
  try { al = await api(`/albums/${artist}/${rg}`); } catch (e) { openModal(`<div class="empty">${esc(e.message)}</div>`); return; }
  let rows = "", disc = null;
  const multi = new Set(al.tracks.map((t) => t.disc)).size > 1;
  for (const t of al.tracks) {
    if (multi && t.disc !== disc) { disc = t.disc; rows += `<tr class="disc"><td colspan="5">Disc ${t.disc}</td></tr>`; }
    rows += `<tr class="${t.owned ? "" : "missing"}"><td>${t.owned ? '<span class="ok">✔</span>' : '<span class="miss">✘</span>'}</td>
      <td>${t.position}</td><td class="title">${esc(t.title)}</td><td>${fmtTime(t.length_ms)}</td>
      <td>${t.file ? `<span class="tag">${esc(t.file.codec)}</span>${t.file.bitrate ? `<span class="tag">${t.file.bitrate} kbps</span>` : ""}` : "Missing"}</td></tr>`;
  }
  const extra = al.extra_files.length ? `<h3>Other files in your library for this album</h3><table>${al.extra_files.map((f) =>
    `<tr><td>${esc(f.title || "")}</td><td class="small">${esc(f.path)}</td></tr>`).join("")}</table>` : "";
  openModal(`
    <div class="modal-head"><div class="poster" style="background-image:url(/api/cover/album/${rg})"></div>
      <div style="flex:1"><h2>${esc(al.title)}</h2>
        <div class="small">${esc(al.primary_type)}${al.secondary_types.length ? " · " + esc(al.secondary_types.join(", ")) : ""} · ${year(al.date)}</div>
        <p>${al.owned} of ${al.total ?? "?"} tracks in your library</p>${bar(al.owned, al.total)}
        <p><a class="small" target="_blank" rel="noopener" href="https://musicbrainz.org/release-group/${rg}">View on MusicBrainz ↗</a></p></div>
      <button class="modal-close" onclick="closeModal()">✕</button></div>
    <div class="modal-body"><table><thead><tr><th></th><th>#</th><th>Title</th><th>Length</th><th>File</th></tr></thead>
      <tbody>${rows || '<tr><td colspan="5" class="empty">No track list available from MusicBrainz.</td></tr>'}</tbody></table>${extra}</div>`);
}
function openModal(html) { $("#modal-box").innerHTML = html; $("#modal").classList.remove("hidden"); }
function closeModal() { $("#modal").classList.add("hidden"); }
$("#modal").addEventListener("click", (e) => { if (e.target.id === "modal") closeModal(); });
document.addEventListener("keydown", (e) => e.key === "Escape" && closeModal());

/* ---------- settings ---------- */
async function showSettings() {
  const s = await api("/settings");
  const box = (name, list, all) => all.map((t) =>
    `<label class="chk"><input type="checkbox" data-${name}="${esc(t)}" ${list.includes(t) ? "checked" : ""}> ${esc(t)}</label>`).join("");
  openModal(`<div class="modal-head"><div><h2>Settings</h2>
    <p class="small">Choose which release types count towards completion. Anything you already own is always shown.</p></div>
    <button class="modal-close" onclick="closeModal()">✕</button></div>
    <div class="modal-body"><h3>Primary types</h3>${box("primary", s.primary_types, PRIMARY_ORDER)}
    <h3>Secondary types</h3><p class="small">“Studio” = releases without any secondary type.</p>${box("secondary", s.secondary_types, SECONDARY)}
    <p><button class="primary" id="save-settings">Save</button></p></div>`);
  $("#save-settings").onclick = async () => {
    const pick = (n) => [...document.querySelectorAll(`[data-${n}]:checked`)].map((e) => e.dataset[n]);
    await api("/settings", { method: "PUT", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ primary_types: pick("primary"), secondary_types: pick("secondary") }) });
    closeModal();
    await startScan(false);   // fills in track counts for newly wanted types
    route();
  };
}
$("#nav-settings").onclick = (e) => { e.preventDefault(); showSettings(); };

/* ---------- router ---------- */
function route(soft) {
  const m = location.hash.match(/^#\/artist\/([0-9a-f-]+)/);
  closeModal();
  if (m) showArtist(m[1]); else showLibrary(soft === true);
}
window.addEventListener("hashchange", () => route());
route();
pollStatus();
