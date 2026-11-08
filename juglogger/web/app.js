// JugLogger UI. Plain JS, no build step.

const app = document.getElementById("app");
const banner = document.getElementById("banner");
let status = null;
let pollTimer = null;
let tickTimer = null;

const prefs = loadPrefs();

// ---------- helpers ----------

function loadPrefs() {
  const defaults = { theme: "light", crop: true, backdrop: "gradient", holds: "all" };
  try { return { ...defaults, ...JSON.parse(localStorage.getItem("juglogger.prefs") || "{}"), photo: null }; }
  catch { return { ...defaults, photo: null }; }
}
function savePrefs() {
  try {
    const { photo, ...rest } = prefs;
    localStorage.setItem("juglogger.prefs", JSON.stringify(rest));
  } catch { /* private mode: fine */ }
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
    body: options.body ? JSON.stringify(options.body) : undefined,
  });
  if (!response.ok) {
    let message = response.statusText;
    try { message = (await response.json()).detail || message; } catch {}
    throw new Error(typeof message === "string" ? message : JSON.stringify(message));
  }
  return response.json();
}

const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const parseTime = (s) => new Date(s.replace(" ", "T"));
const pad = (n) => String(n).padStart(2, "0");

function clock(ms) {
  const s = Math.max(0, Math.floor(ms / 1000));
  return `${pad(Math.floor(s / 3600))}:${pad(Math.floor((s % 3600) / 60))}:${pad(s % 60)}`;
}
function sessionTitle(startedAt) {
  const d = parseTime(startedAt);
  const h = d.getHours();
  const part = h < 5 ? "Night" : h < 12 ? "Morning" : h < 17 ? "Afternoon" : h < 21 ? "Evening" : "Night";
  return `${d.toLocaleDateString(undefined, { weekday: "long" })} ${part} Board Session`;
}
function dateLine(s) {
  const a = parseTime(s.started_at);
  const opts = { hour: "numeric", minute: "2-digit" };
  const end = s.ended_at ? parseTime(s.ended_at).toLocaleTimeString(undefined, opts) : "now";
  return `${a.toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" })} · ${a.toLocaleTimeString(undefined, opts)} – ${end}`;
}
function toLocalInput(s) { return s ? s.slice(0, 16).replace(" ", "T") : ""; }
function nowLocalInput(offsetMinutes = 0) {
  const d = new Date(Date.now() + offsetMinutes * 60000);
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

function toast(message) {
  const el = document.createElement("div");
  el.className = "toast";
  el.textContent = message;
  document.body.append(el);
  setTimeout(() => el.remove(), 2200);
}
function showError(message) {
  banner.hidden = !message;
  banner.textContent = message || "";
}

function triesText(c) {
  if (c.flash) return `<span class="flash">Flash</span>`;
  if (c.sent) return `${c.tries_to_send ?? c.tries} tries`;
  return `Project · ${c.tries} ${c.tries === 1 ? "try" : "tries"}`;
}
function climbRow(c) {
  const meta = [c.angle != null ? `${c.angle}°` : "—"];
  if (c.is_mirror) meta.push("Mirrored");
  if (c.layout) meta.push(c.layout.replace(/^Tension Board 2\s*/i, "TB2 "));
  if (c.is_benchmark) meta.push("Benchmark");
  return `
    <div class="climb ${c.sent ? "" : "project"}">
      <div class="name">${esc(c.name)}</div>
      <div class="grade">${esc(c.grade || "—")}</div>
      <div class="meta">${meta.map(esc).join(" · ")} · ${triesText(c)}</div>
    </div>`;
}

function stopTimers() {
  clearInterval(pollTimer); clearInterval(tickTimer);
  pollTimer = tickTimer = null;
}

// ---------- routing ----------

async function route() {
  stopTimers();
  showError("");
  const hash = location.hash || "#/";
  document.querySelectorAll("nav a").forEach((a) =>
    a.classList.toggle("on", (a.dataset.nav === "history") === hash.startsWith("#/history") && !hash.startsWith("#/session")));
  try {
    status = await api("/api/status");
    if (status.error) showError(status.error);
    if (!status.board_ready) return renderSetupNeeded();
    if (!status.account) return renderConnect();
    const m = hash.match(/^#\/session\/(\d+)/);
    if (m) return renderSession(Number(m[1]));
    if (hash.startsWith("#/history")) return renderHistory();
    return status.active_session_id ? renderLive(status.active_session_id) : renderIdle();
  } catch (error) {
    app.innerHTML = `<div class="empty">${esc(error.message)}</div>`;
  }
}
window.addEventListener("hashchange", route);

// ---------- views ----------

function renderSetupNeeded() {
  app.innerHTML = `
    <div class="card" style="max-width:560px;margin:48px auto">
      <div class="eyebrow">Setup</div>
      <h2 style="margin-top:8px">Board data missing</h2>
      <p class="muted">Run this once in the repo folder, then reload:</p>
      <pre class="mono" style="background:var(--bg);padding:12px;border-radius:10px">python -m juglogger setup</pre>
    </div>`;
}

function renderConnect() {
  app.innerHTML = `
    <div style="max-width:420px;margin:48px auto">
      <div class="eyebrow">Connect</div>
      <h1>Tension Board 2</h1>
      <p class="muted">Sign in with your Tension app account. Your password goes straight to Tension; only the session token is kept, on this computer.</p>
      <form class="form" id="connect">
        <label class="field"><span class="eyebrow">Username</span><input name="username" autocomplete="username" required></label>
        <label class="field"><span class="eyebrow">Password</span><input name="password" type="password" autocomplete="current-password" ${status.demo ? "" : "required"}></label>
        <button class="btn primary" type="submit">Connect</button>
      </form>
    </div>`;
  document.getElementById("connect").onsubmit = async (e) => {
    e.preventDefault();
    const form = new FormData(e.target);
    const btn = e.target.querySelector("button");
    btn.disabled = true; btn.textContent = "Connecting…";
    try {
      await api("/api/connect", { method: "POST", body: Object.fromEntries(form) });
      route();
    } catch (error) {
      showError(error.message);
      btn.disabled = false; btn.textContent = "Connect";
    }
  };
}

function renderIdle() {
  app.innerHTML = `
    <div class="timer-wrap">
      <div class="eyebrow">TB2 · ${esc(status.account.username)}${status.demo ? " · demo" : ""}</div>
      <div class="timer idle">00:00:00</div>
      <button class="go" id="start">START</button>
      <p class="muted" style="margin-top:28px;max-width:360px">Start the timer, climb, and log your sends and attempts in the Tension app as usual. Stop when you're done.</p>
    </div>
    <details class="card" style="max-width:560px;margin:24px auto 0">
      <summary class="row" style="justify-content:space-between"><span><span class="eyebrow">Already climbed?</span><h2 style="margin-top:4px">Log a past session</h2></span><span class="muted">+</span></summary>
      <form class="form" id="past" style="margin-top:16px;max-width:none">
        <div class="row">
          <label class="field" style="flex:1"><span class="eyebrow">Start</span><input type="datetime-local" name="started_at" required value="${nowLocalInput(-120)}"></label>
          <label class="field" style="flex:1"><span class="eyebrow">End</span><input type="datetime-local" name="ended_at" required value="${nowLocalInput()}"></label>
        </div>
        <button class="btn" type="submit">Create session</button>
      </form>
    </details>`;
  document.getElementById("start").onclick = async (e) => {
    e.target.disabled = true;
    try {
      const { id } = await api("/api/sessions/start", { method: "POST" });
      status.active_session_id = id;
      renderLive(id);
    } catch (error) { showError(error.message); e.target.disabled = false; }
  };
  document.getElementById("past").onsubmit = async (e) => {
    e.preventDefault();
    try {
      const { id } = await api("/api/sessions", { method: "POST", body: Object.fromEntries(new FormData(e.target)) });
      location.hash = `#/session/${id}`;
    } catch (error) { showError(error.message); }
  };
}

async function renderLive(id) {
  let session = await api(`/api/sessions/${id}`);
  // Keep the clock honest even if this browser's time differs from the server's.
  const skew = Date.now() - parseTime(session.now).getTime();
  const started = parseTime(session.started_at).getTime() + skew;

  app.innerHTML = `
    <div class="timer-wrap">
      <div class="eyebrow"><span class="live-dot"></span>Recording · TB2</div>
      <div class="timer" id="clock">${clock(Date.now() - started)}</div>
      <button class="go stop" id="stop">STOP</button>
    </div>
    <div class="card" style="max-width:640px;margin:0 auto">
      <div class="row" style="justify-content:space-between">
        <div><div class="eyebrow">This session</div><h2 id="count" style="margin-top:4px"></h2></div>
        <span class="mono muted" id="synced"></span>
      </div>
      <div class="climbs" id="climbs"></div>
    </div>`;

  const paint = () => {
    document.getElementById("count").textContent =
      `${session.send_count} sent · ${session.climb_count} ${session.climb_count === 1 ? "climb" : "climbs"}`;
    document.getElementById("climbs").innerHTML = session.climbs.length
      ? [...session.climbs].reverse().map(climbRow).join("")
      : `<div class="empty">Climbs you log in the Tension app show up here.</div>`;
    document.getElementById("synced").textContent = session.last_fetch_at
      ? `synced ${parseTime(session.last_fetch_at).toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit", second: "2-digit" })}` : "";
    showError(session.error);
  };
  paint();

  tickTimer = setInterval(() => {
    const el = document.getElementById("clock");
    if (el) el.textContent = clock(Date.now() - started);
  }, 1000);
  pollTimer = setInterval(async () => {
    try { session = await api(`/api/sessions/${id}`); paint(); } catch {}
  }, 15000);

  document.getElementById("stop").onclick = async (e) => {
    e.target.disabled = true;
    e.target.textContent = "…";
    stopTimers();
    try {
      await api(`/api/sessions/${id}/stop`, { method: "POST" });
      location.hash = `#/session/${id}`;
    } catch (error) { showError(error.message); e.target.disabled = false; e.target.textContent = "STOP"; }
  };
}

async function renderHistory() {
  const sessions = await api("/api/sessions");
  app.innerHTML = `
    <div class="eyebrow">History</div>
    <h1 style="margin-bottom:24px">Sessions</h1>
    <div class="history">
      ${sessions.length ? sessions.map((s) => `
        <a href="#/session/${s.id}">
          <div><div class="mono muted">${esc(dateLine(s))}</div><div style="font-weight:600;font-size:18px;margin-top:2px">${s.active ? '<span class="live-dot"></span>' : ""}${esc(sessionTitle(s.started_at))}</div></div>
          <div><div class="n">${s.send_count}</div><div class="l">Sends</div></div>
          <div class="hide-sm"><div class="n">${s.climb_count}</div><div class="l">Climbs</div></div>
          <div><div class="n accent">${esc(s.top_grade || "—")}</div><div class="l">Top</div></div>
        </a>`).join("") : `<div class="empty">No sessions yet.</div>`}
    </div>`;
}

async function renderSession(id, refresh = true) {
  if (!app.querySelector(`[data-session="${id}"]`)) app.innerHTML = `<div class="empty">Loading…</div>`;
  const s = await api(`/api/sessions/${id}${refresh ? "?refresh=1" : ""}`);
  if (s.active) return renderLive(id);
  showError(s.error);
  const hardest = s.hardest;
  const version = Date.now();

  app.innerHTML = `
    <div data-session="${id}">
      <div class="eyebrow">${esc(dateLine(s))}</div>
      <h1>${esc(sessionTitle(s.started_at))}</h1>
      <div class="stats" style="margin-top:24px">
        <div><div class="eyebrow">Time</div><div class="v">${esc(s.duration)}</div></div>
        <div><div class="eyebrow">Sends</div><div class="v">${s.send_count}</div></div>
        <div><div class="eyebrow">Top grade</div><div class="v accent">${esc(hardest?.grade || "—")}</div></div>
        <div><div class="eyebrow">Flashes</div><div class="v">${s.flash_count}</div></div>
        <div><div class="eyebrow">Attempts</div><div class="v">${s.total_tries}</div></div>
      </div>

      <div class="section-head">
        <div><div class="eyebrow">Share</div><h2 style="margin-top:4px">Overlays</h2></div>
        <div class="controls">
          <div class="seg" data-pref="theme"><button data-v="light">White</button><button data-v="dark">Black</button></div>
          <div class="seg" data-pref="crop"><button data-v="true">Sticker</button><button data-v="false">Story</button></div>
          <div class="seg" data-pref="backdrop"><button data-v="gradient">Sky</button><button data-v="dark">Dark</button><button data-v="light">Light</button><button data-v="photo">Photo</button></div>
          <input type="file" id="photo" accept="image/*" hidden>
        </div>
      </div>
      <div class="gallery" id="gallery">
        ${status.graphics.map((g) => `
          <div class="tile" data-kind="${g.kind}">
            <div class="tile-head"><span class="eyebrow">${esc(g.title)}</span>
              <div class="tile-actions">
                <button class="btn sm ghost" data-act="copy" title="Copy image">Copy</button>
                <button class="btn sm" data-act="save">Save</button>
              </div>
            </div>
            <div class="preview"><span class="spin">rendering…</span><img alt="${esc(g.title)} overlay" hidden></div>
            ${g.kind === "board" && s.climbs.length ? `
              <div class="seg" data-pref="holds"><button data-v="all">All holds</button><button data-v="lit">Lit only</button></div>
              <select data-climb>
                ${[...s.climbs].sort((a, b) => (b.sent - a.sent)).map((c) => `<option value="${esc(c.climb_uuid)}" ${hardest && c.climb_uuid === hardest.climb_uuid ? "selected" : ""}>${esc(c.name)} · ${esc(c.grade || "—")}${c.sent ? "" : " (project)"}</option>`).join("")}
              </select>` : ""}
          </div>`).join("")}
      </div>

      <div class="section-head"><div><div class="eyebrow">Log</div><h2 style="margin-top:4px">${s.climb_count} ${s.climb_count === 1 ? "climb" : "climbs"}</h2></div>
        <button class="btn sm" id="refresh">Refresh from Tension</button></div>
      <div class="card climbs" style="margin-top:0">${s.climbs.length ? s.climbs.map(climbRow).join("") : `<div class="empty">No climbs found between the start and end of this session. Logged them later? Refresh, or widen the times below.</div>`}</div>

      <details class="card" style="margin-top:24px">
        <summary><span class="eyebrow">Session settings</span></summary>
        <form class="form" id="edit" style="margin-top:16px;max-width:none">
          <div class="row">
            <label class="field" style="flex:1"><span class="eyebrow">Start</span><input type="datetime-local" name="started_at" value="${toLocalInput(s.started_at)}"></label>
            <label class="field" style="flex:1"><span class="eyebrow">End</span><input type="datetime-local" name="ended_at" value="${toLocalInput(s.ended_at)}"></label>
          </div>
          <div class="row"><button class="btn" type="submit">Save times</button><button class="btn danger ghost" type="button" id="delete">Delete session</button></div>
        </form>
      </details>
    </div>`;

  const gallery = document.getElementById("gallery");
  const url = (tile, extra = "") => {
    const climb = tile.querySelector("[data-climb]")?.value;
    return `/api/sessions/${id}/graphics/${tile.dataset.kind}.png?theme=${prefs.theme}&crop=${prefs.crop}${tile.dataset.kind === "board" ? `&holds=${prefs.holds}` : ""}${climb ? `&climb=${encodeURIComponent(climb)}` : ""}&v=${version}${extra}`;
  };
  const applyBackdrop = () => {
    const map = {
      gradient: "linear-gradient(170deg, #1d3a6b 0%, #4f6ea3 55%, #c9a27a 100%)",
      dark: "#141416", light: "#ecebe8",
      photo: prefs.photo ? `center / cover no-repeat url("${prefs.photo}")` : "linear-gradient(170deg, #1d3a6b, #4f6ea3)",
    };
    gallery.style.setProperty("--backdrop", map[prefs.backdrop]);
  };
  const paintTile = (tile) => {
    const img = tile.querySelector("img");
    const spin = tile.querySelector(".spin");
    const preview = tile.querySelector(".preview");
    preview.classList.toggle("frame", !prefs.crop);
    spin.hidden = false;
    img.onload = () => { spin.hidden = true; img.hidden = false; };
    img.onerror = () => { spin.textContent = "couldn't render"; };
    img.src = url(tile);
  };
  const paintAll = () => {
    document.querySelectorAll(".seg[data-pref]").forEach((seg) =>
      seg.querySelectorAll("button").forEach((b) => b.classList.toggle("on", String(prefs[seg.dataset.pref]) === b.dataset.v)));
    applyBackdrop();
    gallery.querySelectorAll(".tile").forEach(paintTile);
  };

  document.querySelectorAll(".seg[data-pref]").forEach((seg) => seg.addEventListener("click", (e) => {
    const v = e.target.dataset.v;
    if (!v) return;
    const key = seg.dataset.pref;
    if (key === "backdrop" && v === "photo" && !prefs.photo) return document.getElementById("photo").click();
    prefs[key] = key === "crop" ? v === "true" : v;
    savePrefs();
    if (key === "backdrop") {
      seg.querySelectorAll("button").forEach((b) => b.classList.toggle("on", b.dataset.v === v));
      applyBackdrop();
    } else paintAll();
  }));
  document.getElementById("photo").onchange = (e) => {
    const file = e.target.files[0];
    if (!file) return;
    if (prefs.photo) URL.revokeObjectURL(prefs.photo);
    prefs.photo = URL.createObjectURL(file);
    prefs.backdrop = "photo";
    paintAll();
  };

  gallery.addEventListener("change", (e) => {
    if (e.target.matches("[data-climb]")) paintTile(e.target.closest(".tile"));
  });
  gallery.addEventListener("click", async (e) => {
    const tile = e.target.closest(".tile");
    if (!tile) return;
    const act = e.target.dataset.act;
    if (act === "save") {
      const a = document.createElement("a");
      a.href = url(tile, "&download=1");
      a.download = `juglogger-${id}-${tile.dataset.kind}.png`;
      a.click();
    } else if (act === "copy") {
      try {
        const blob = await (await fetch(url(tile))).blob();
        if (navigator.share && navigator.canShare && /Mobi|Android|iPhone/i.test(navigator.userAgent)) {
          const file = new File([blob], `juglogger-${tile.dataset.kind}.png`, { type: "image/png" });
          if (navigator.canShare({ files: [file] })) return navigator.share({ files: [file] });
        }
        await navigator.clipboard.write([new ClipboardItem({ "image/png": blob })]);
        toast("Copied. Paste it into your story.");
      } catch (error) { toast("Couldn't copy. Use Save instead."); }
    } else if (e.target.closest(".preview")) {
      const box = document.createElement("div");
      box.className = "lightbox";
      box.style.setProperty("--backdrop", gallery.style.getPropertyValue("--backdrop"));
      box.innerHTML = `<div class="preview ${prefs.crop ? "" : "frame"}"><img src="${url(tile)}" alt=""></div>`;
      box.onclick = () => box.remove();
      document.body.append(box);
    }
  });

  document.getElementById("refresh").onclick = async (e) => {
    e.target.disabled = true; e.target.textContent = "Refreshing…";
    await renderSession(id, true);
  };
  document.getElementById("edit").onsubmit = async (e) => {
    e.preventDefault();
    try {
      await api(`/api/sessions/${id}`, { method: "PATCH", body: Object.fromEntries(new FormData(e.target)) });
      toast("Saved");
      renderSession(id, true);
    } catch (error) { showError(error.message); }
  };
  document.getElementById("delete").onclick = async () => {
    if (!confirm("Delete this session? Your climbs stay in the Tension app.")) return;
    await api(`/api/sessions/${id}`, { method: "DELETE" });
    location.hash = "#/history";
  };

  paintAll();
}

route();
