/* HBlive – Dashboard für die Herren des MTV Hondelage.
 * Lädt data.json (vom Server oder statisch gebaut) regelmäßig neu und rendert
 * Live-/Heute-Ansicht, Mannschaftskarten, Spielplan und Tabellen. */
(() => {
  "use strict";

  const GAME_LIVE_MIN = 80;      // 2×30 min + Pause + Puffer
  const POLL_HOT = 30_000;       // während/um eigene Spiele
  const POLL_IDLE = 120_000;
  const OUTCOME = { W: "S", D: "U", L: "N" };
  const OUTCOME_LONG = { W: "Sieg", D: "Unentschieden", L: "Niederlage" };

  const $ = (sel) => document.querySelector(sel);
  const store = {
    get(k, d) { try { return localStorage.getItem(k) ?? d; } catch { return d; } },
    set(k, v) { try { localStorage.setItem(k, v); } catch { /* egal */ } },
  };

  const state = {
    data: null,
    raw: "",
    team: store.get("hblive.team", "all"),
    view: store.get("hblive.view", "upcoming"),
    error: null,
    lastFetch: null,
    known: new Map(),   // key -> result_text, für "neues Ergebnis"-Hinweise
    flash: new Set(),
    pollTimer: null,
  };

  // ------------------------------------------------------------------ helpers
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const ko = (g) => (g.kickoff ? new Date(g.kickoff) : null);
  const gameKey = (t, g) => `${t.key}:${g.number}`;
  const dfDay = new Intl.DateTimeFormat("de-DE", { weekday: "short", day: "2-digit", month: "2-digit", timeZone: "Europe/Berlin" });
  const dfMonth = new Intl.DateTimeFormat("de-DE", { month: "long", year: "numeric", timeZone: "Europe/Berlin" });
  const dfTime = new Intl.DateTimeFormat("de-DE", { hour: "2-digit", minute: "2-digit", timeZone: "Europe/Berlin" });
  const dfKey = new Intl.DateTimeFormat("en-CA", { year: "numeric", month: "2-digit", day: "2-digit", timeZone: "Europe/Berlin" });
  const dayKey = (d) => dfKey.format(d);

  function phase(g, now = Date.now()) {
    if (g.state === "finished") return "finished";
    if (g.state === "special") return "special";
    const k = ko(g);
    if (!k) return "scheduled";
    const min = (now - k.getTime()) / 60000;
    if (min < 0) return "scheduled";
    if (min < GAME_LIVE_MIN) return "live";
    return "pending";
  }

  function countdown(ms) {
    if (ms <= 0) return "jetzt";
    const s = Math.floor(ms / 1000), d = Math.floor(s / 86400), h = Math.floor((s % 86400) / 3600), m = Math.floor((s % 3600) / 60);
    if (d >= 2) return `in ${d} Tagen`;
    if (d === 1) return `in 1 Tag ${h} Std`;
    if (h > 0) return `in ${h} Std ${m} Min`;
    return `in ${m}:${String(s % 60).padStart(2, "0")} Min`;
  }

  function ago(iso) {
    if (!iso) return "–";
    const min = Math.round((Date.now() - new Date(iso).getTime()) / 60000);
    if (min < 1) return "gerade eben";
    if (min < 60) return `vor ${min} Min`;
    const h = Math.round(min / 60);
    if (h < 24) return `vor ${h} Std`;
    return new Date(iso).toLocaleString("de-DE", { dateStyle: "short", timeStyle: "short" });
  }

  const visibleTeams = () => (state.data?.teams ?? []).filter((t) => state.team === "all" || t.key === state.team);
  const allGames = (teams) => teams.flatMap((t) => t.games.map((g) => ({ t, g })));

  function scoreFor(g) {
    // Ergebnis aus Sicht des Spielplans (Heim:Gast)
    if (g.home_goals != null) return `${g.home_goals}:${g.away_goals}`;
    if (g.result_text && g.state === "special") return g.result_text;
    return "–:–";
  }

  function resBadge(g) {
    if (!g.outcome) return `<span class="res none" title="kein Ergebnis">–</span>`;
    const label = OUTCOME_LONG[g.outcome];
    return `<span class="res ${g.outcome}" title="${label}" aria-label="${label}">${OUTCOME[g.outcome]}</span>`;
  }

  // ------------------------------------------------------------------ render
  function renderFilter() {
    const teams = state.data?.teams ?? [];
    const opts = [{ key: "all", label: "Alle" }, ...teams.map((t) => ({ key: t.key, label: t.label }))];
    if (!opts.some((o) => o.key === state.team)) state.team = "all";
    $("#team-filter").innerHTML = opts
      .map((o) => `<button type="button" role="tab" data-v="${esc(o.key)}" aria-selected="${o.key === state.team}">${esc(o.label)}</button>`)
      .join("");
    document.querySelectorAll("#schedule-filter button").forEach((b) => b.setAttribute("aria-selected", String(b.dataset.v === state.view)));
  }

  function renderToday() {
    const el = $("#today");
    const now = Date.now();
    const today = dayKey(new Date());
    const list = allGames(visibleTeams()).filter(({ g }) => {
      const k = ko(g);
      if (!k) return false;
      const p = phase(g, now);
      return dayKey(k) === today || p === "live" || (p === "pending" && now - k.getTime() < 12 * 3600e3);
    });
    if (!list.length) { el.hidden = true; el.innerHTML = ""; return; }
    list.sort((a, b) => ko(a.g) - ko(b.g));
    el.hidden = false;
    el.innerHTML = list.map(({ t, g }) => {
      const p = phase(g, now);
      const k = ko(g);
      let pill, center;
      if (p === "live") {
        pill = `<span class="pill live">Live</span>`;
        center = `<div class="countdown tnum" data-live="${k.getTime()}">${Math.max(1, Math.floor((now - k) / 60000))}'</div><small>läuft · Ergebnis folgt in nuLiga</small>`;
      } else if (p === "pending") {
        pill = `<span class="pill pending">Ergebnis ausstehend</span>`;
        center = `<div class="score tnum">–:–</div><small>Anpfiff war ${dfTime.format(k)} Uhr</small>`;
      } else if (p === "finished" || p === "special") {
        pill = `<span class="pill">Beendet</span>`;
        const ht = g.halftime ? `<small>Halbzeit ${g.halftime.home}:${g.halftime.away}</small>` : g.result_note ? `<small>${esc(g.result_note)}</small>` : "";
        center = `<div class="score tnum">${esc(scoreFor(g))}</div>${ht}`;
      } else {
        pill = `<span class="pill">Heute ${dfTime.format(k)} Uhr</span>`;
        center = `<div class="countdown tnum" data-ko="${k.getTime()}">${countdown(k - now)}</div><small>bis Anpfiff</small>`;
      }
      return `
        <article class="match-hero ${p === "live" ? "is-live" : ""}">
          <div class="eyebrow"><span><span class="pill team">${esc(t.label)}</span> ${esc(g.competition || "")}</span>${pill}</div>
          <div class="teams-line">
            <div class="side home ${g.is_home ? "own" : ""}">${esc(g.home)}</div>
            <div class="center">${center}</div>
            <div class="side away ${!g.is_home ? "own" : ""}">${esc(g.away)}</div>
          </div>
          <div class="meta">
            <span>${esc(dfDay.format(k))} · ${dfTime.format(k)} Uhr</span>
            ${g.venue_name ? `<span>📍 ${venueLink(g)}</span>` : ""}
            ${g.referees ? `<span>SR: ${esc(g.referees)}</span>` : ""}
          </div>
        </article>`;
    }).join("");
  }

  function venueLink(g) {
    const name = g.venue_name || g.venue_code || "";
    if (!name) return "";
    const q = encodeURIComponent(name.replace(/^([^,]+),\s*(.*)$/, "$2 $1"));
    return `<a href="https://www.google.com/maps/search/?api=1&query=${q}" target="_blank" rel="noopener" title="Halle in Karte öffnen">${esc(name)}</a>`;
  }

  function renderTeams() {
    const now = Date.now();
    $("#teams").innerHTML = visibleTeams().map((t) => {
      const comp = t.competitions[0] || {};
      const row = (comp.standings || []).find((s) => s.is_own) || {};
      const n = (comp.standings || []).length;
      const played = t.games.filter((g) => g.outcome);
      const form = played.slice(-5);
      const next = t.games.find((g) => ["scheduled", "live"].includes(phase(g, now)));
      const last = [...t.games].reverse().find((g) => g.state !== "scheduled");
      const diff = row.goal_diff ?? (row.goals_for != null ? row.goals_for - row.goals_against : null);

      const nextHtml = next ? (() => {
        const k = ko(next);
        const p = phase(next, now);
        return `<div class="label">Nächstes Spiel · ${next.is_home ? "Heim" : "Auswärts"}</div>
          <div class="opp" title="${esc(next.opponent)}">${esc(next.opponent)}</div>
          <div class="when">${k ? `${esc(dfDay.format(k))} · ${dfTime.format(k)} Uhr` : esc(next.date)}</div>
          <div class="when tnum">${p === "live" ? `<span class="pill live">Live</span>` : k ? `<span data-ko="${k.getTime()}">${countdown(k - now)}</span>` : ""}</div>`;
      })() : `<div class="label">Nächstes Spiel</div><div class="when">Keine weiteren Spiele</div>`;

      const lastHtml = last ? `<div class="label">Letztes Spiel · ${last.is_home ? "Heim" : "Auswärts"}</div>
          <div class="opp" title="${esc(last.opponent)}">${esc(last.opponent)}</div>
          <div class="big tnum">${resBadge(last)} ${last.goals_for != null ? `${last.goals_for}:${last.goals_against}` : esc(last.result_text || "")}</div>
          <div class="when">${ko(last) ? esc(dfDay.format(ko(last))) : esc(last.date)}${last.result_note ? ` · ${esc(last.result_note)}` : ""}</div>`
        : `<div class="label">Letztes Spiel</div><div class="when">Noch keine Spiele</div>`;

      return `
        <article class="card">
          <div class="card-head">
            <div><h2>${esc(t.label)}</h2><div class="league">${esc(comp.league || "")}</div></div>
            ${comp.group_url ? `<a href="${esc(comp.group_url)}" target="_blank" rel="noopener">nuLiga ↗</a>` : ""}
          </div>
          <div class="kpis">
            <div class="kpi"><div class="label">Platz</div><div class="value tnum">${row.rank ?? "–"}.<small> / ${n || "–"}</small></div></div>
            <div class="kpi"><div class="label">Punkte</div><div class="value tnum">${row.points_plus ?? "–"}<small>:${row.points_minus ?? "–"}</small></div></div>
            <div class="kpi"><div class="label">Bilanz S-U-N</div><div class="value tnum">${row.won ?? 0}-${row.drawn ?? 0}-${row.lost ?? 0}</div></div>
            <div class="kpi"><div class="label">Tore</div><div class="value tnum">${diff != null ? (diff > 0 ? "+" : "") + diff : "–"}</div><div class="hint tnum">${row.goals_for ?? "–"}:${row.goals_against ?? "–"}</div></div>
          </div>
          <div class="form"><span class="label">Form</span>${form.length ? form.map((g) => {
            const r = g.goals_for != null ? `${g.goals_for}:${g.goals_against}` : g.result_note || "";
            const label = `${OUTCOME_LONG[g.outcome]} ${r} ${g.is_home ? "gegen" : "bei"} ${g.opponent}`;
            return `<span class="res ${g.outcome}" title="${esc(label)}" aria-label="${esc(label)}">${OUTCOME[g.outcome]}</span>`;
          }).join("") : `<span class="muted">noch keine Ergebnisse</span>`}</div>
          <div class="split"><div class="mini">${nextHtml}</div><div class="mini">${lastHtml}</div></div>
        </article>`;
    }).join("") || `<p class="empty">Keine Mannschaften gefunden.</p>`;
  }

  function renderSchedule() {
    const now = Date.now();
    const teams = visibleTeams();
    let list = allGames(teams).sort((a, b) => (a.g.kickoff || "").localeCompare(b.g.kickoff || ""));
    const nextGame = list.find(({ g }) => phase(g, now) === "scheduled");
    if (state.view === "upcoming") list = list.filter(({ g }) => ["scheduled", "live", "pending"].includes(phase(g, now)));
    if (state.view === "results") list = list.filter(({ g }) => ["finished", "special"].includes(phase(g, now))).reverse();
    if (!list.length) {
      $("#schedule").innerHTML = `<p class="empty">${state.view === "results" ? "Noch keine Ergebnisse." : "Keine Spiele."}</p>`;
      return;
    }
    const showTeam = teams.length > 1;
    let month = "";
    $("#schedule").innerHTML = list.map(({ t, g }) => {
      const k = ko(g);
      const p = phase(g, now);
      const m = k ? dfMonth.format(k) : "";
      const head = m !== month ? `<div class="month">${esc((month = m))}</div>` : "";
      const tags = [];
      if (showTeam) tags.push(`<span class="tag sm-only">${esc(t.label)}</span>`);
      if (g.time_marker === "v") tags.push(`<span class="tag moved" title="${esc(g.time_note || "verlegt")}">verlegt</span>`);
      let result;
      if (p === "finished") {
        result = `${resBadge(g)}<div><span class="tnum">${scoreFor(g)}</span>${g.halftime ? `<small class="tnum">HZ ${g.halftime.home}:${g.halftime.away}</small>` : ""}</div>`;
      } else if (p === "special") {
        result = `${resBadge(g)}<div><span>${esc(g.result_text || "")}</span><small>${esc(g.result_note || "")}</small></div>`;
      } else if (p === "live") {
        result = `<span class="pill live">Live</span>`;
      } else if (p === "pending") {
        result = `<span class="pill pending">ausstehend</span>`;
      } else {
        result = k ? `<small class="muted" data-ko="${k.getTime()}">${countdown(k - now)}</small>` : "";
      }
      const links = [];
      if (g.report_url) links.push(`<a href="${esc(g.report_url)}" target="_blank" rel="noopener">Spielbericht</a>`);
      if (g.report_pdf_url) links.push(`<a href="${esc(g.report_pdf_url)}" target="_blank" rel="noopener">PDF</a>`);
      const cls = ["game", g === nextGame?.g ? "next" : "", p === "live" ? "is-live" : "", state.flash.has(gameKey(t, g)) ? "flash" : ""].join(" ");
      return `${head}
        <div class="${cls}">
          <div class="date tnum">${k ? esc(dfDay.format(k)) : esc(g.date)}<small>${g.time ? esc(g.time) + " Uhr" : ""}</small></div>
          <div class="teamcol">${showTeam ? `<span class="pill team">${esc(t.label)}</span>` : `<span class="muted">Nr. ${esc(g.number)}</span>`}</div>
          <div class="match">
            <div class="${g.is_home ? "own" : ""}"><span class="ha">H</span>${esc(g.home)}</div>
            <div class="${!g.is_home ? "own" : ""}"><span class="ha">G</span>${esc(g.away)}</div>
          </div>
          <div class="result">${result}</div>
          <div class="info">
            <div>${tags.join("")}${venueLink(g)}</div>
            <div>${g.referees ? `SR: ${esc(g.referees)}` : ""}${links.length ? (g.referees ? " · " : "") + links.join(" · ") : ""}</div>
          </div>
        </div>`;
    }).join("");
  }

  function renderTables() {
    $("#tables").innerHTML = visibleTeams().flatMap((t) => t.competitions.map((c) => `
      <section class="panel">
        <div class="panel-head"><h2>Tabelle ${esc(t.label)}</h2><span class="muted">${esc(c.league)}</span></div>
        <div class="tbl-wrap">
          <table class="standings">
            <thead><tr><th>#</th><th class="l">Mannschaft</th><th title="Spiele">Sp</th><th class="hide-sm" title="Siege">S</th><th class="hide-sm" title="Unentschieden">U</th><th class="hide-sm" title="Niederlagen">N</th><th title="Tore">Tore</th><th title="Tordifferenz">+/-</th><th title="Punkte">Pkt</th></tr></thead>
            <tbody>${(c.standings || []).map((s) => `
              <tr class="${s.is_own ? "own" : ""}">
                <td>${s.rank ?? ""}</td>
                <td class="l team">${s.team_url ? `<a href="${esc(s.team_url)}" target="_blank" rel="noopener" style="color:inherit">${esc(s.team)}</a>` : esc(s.team)}</td>
                <td>${s.games}</td><td class="hide-sm">${s.won}</td><td class="hide-sm">${s.drawn}</td><td class="hide-sm">${s.lost}</td>
                <td>${s.goals_for ?? ""}:${s.goals_against ?? ""}</td>
                <td>${s.goal_diff > 0 ? "+" : ""}${s.goal_diff ?? ""}</td>
                <td class="pts">${s.points_plus ?? ""}:${s.points_minus ?? ""}</td>
              </tr>`).join("")}
            </tbody>
          </table>
        </div>
      </section>`)).join("");
  }

  function renderStatus() {
    const dot = $("#status-dot");
    const txt = $("#status-text");
    if (state.error && !state.data) { dot.className = "dot err"; txt.textContent = "Daten nicht erreichbar"; return; }
    dot.className = "dot " + (state.error ? "err" : "ok");
    const gen = state.data?.generated_at;
    txt.textContent = (state.error ? "Verbindung gestört · " : "") + `Stand ${ago(gen)}`;
    txt.title = gen ? `Daten von nuLiga abgerufen: ${new Date(gen).toLocaleString("de-DE")}` : "";
    const season = state.data?.teams?.[0]?.competitions?.[0]?.season;
    if (season) $("#season").textContent = `${season} · Spielplan, Ergebnisse & Tabellen`;
    if (state.data?.source) $("#source-link").href = state.data.source;
    $("#generated").textContent = gen ? `Letzter Abruf: ${new Date(gen).toLocaleString("de-DE")}.` : "";
  }

  function render() {
    if (!state.data) { renderStatus(); return; }
    renderFilter();
    renderToday();
    renderTeams();
    renderSchedule();
    renderTables();
    renderStatus();
  }

  // Sekündlich nur Countdown/Live-Minuten aktualisieren; Phasenwechsel -> komplett neu rendern.
  let lastPhaseSig = "";
  function tick() {
    const now = Date.now();
    document.querySelectorAll("[data-ko]").forEach((el) => { el.textContent = countdown(+el.dataset.ko - now); });
    document.querySelectorAll("[data-live]").forEach((el) => { el.textContent = `${Math.max(1, Math.floor((now - +el.dataset.live) / 60000))}'`; });
    const sig = allGames(state.data?.teams ?? []).map(({ g }) => phase(g, now)).join("") + dayKey(new Date());
    if (sig !== lastPhaseSig) { lastPhaseSig = sig; render(); }
  }

  // ------------------------------------------------------------------ data
  function detectChanges(data) {
    const changes = [];
    for (const t of data.teams) for (const g of t.games) {
      const key = gameKey(t, g);
      const val = g.state !== "scheduled" ? g.result_text : null;
      if (state.known.size && state.known.has(key) && state.known.get(key) == null && val != null) {
        changes.push(`${t.label}: ${g.home} – ${g.away} ${val}`);
        state.flash.add(key);
        setTimeout(() => state.flash.delete(key), 3000);
      }
      state.known.set(key, val);
    }
    if (changes.length) toast(`Neues Ergebnis! ${changes.join(" · ")}`);
  }

  async function load() {
    try {
      const res = await fetch("data.json", { cache: "no-cache" });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const text = await res.text();
      state.error = null;
      state.lastFetch = Date.now();
      if (text !== state.raw) {
        const data = JSON.parse(text);
        state.raw = text;
        detectChanges(data);
        state.data = data;
        render();
      } else {
        renderStatus();
      }
    } catch (err) {
      state.error = err;
      renderStatus();
    }
    schedulePoll();
  }

  function schedulePoll() {
    clearTimeout(state.pollTimer);
    const now = Date.now();
    const hot = allGames(state.data?.teams ?? []).some(({ g }) => {
      const k = ko(g);
      if (!k || g.state !== "scheduled") return false;
      return now > k.getTime() - 30 * 60e3 && now < k.getTime() + 5 * 3600e3;
    });
    state.pollTimer = setTimeout(load, state.error ? 15_000 : hot ? POLL_HOT : POLL_IDLE);
  }

  async function manualRefresh() {
    const btn = $("#refresh-btn");
    btn.classList.add("spin");
    btn.disabled = true;
    const before = state.data?.generated_at;
    try {
      const res = await fetch("api/refresh", { method: "POST" });
      if (res.status === 202) {
        // Server scrapt neu – kurz warten, bis neue Daten da sind.
        for (let i = 0; i < 12; i++) {
          await new Promise((r) => setTimeout(r, 2500));
          await load();
          if (state.data?.generated_at !== before) break;
        }
      } else {
        if (res.status === 429) toast("Gerade erst aktualisiert – bitte kurz warten.");
        await load();
      }
    } catch {
      await load();
    } finally {
      btn.classList.remove("spin");
      btn.disabled = false;
    }
  }

  let toastTimer;
  function toast(msg) {
    const el = $("#toast");
    el.textContent = msg;
    el.hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => { el.hidden = true; }, 8000);
  }

  // ------------------------------------------------------------------ events
  function setTheme(t) {
    if (t) document.documentElement.dataset.theme = t; else delete document.documentElement.dataset.theme;
  }
  setTheme(store.get("hblive.theme", ""));

  document.addEventListener("click", (e) => {
    const tb = e.target.closest("#team-filter button");
    if (tb) { state.team = tb.dataset.v; store.set("hblive.team", state.team); render(); return; }
    const vb = e.target.closest("#schedule-filter button");
    if (vb) { state.view = vb.dataset.v; store.set("hblive.view", state.view); renderFilter(); renderSchedule(); }
  });
  $("#refresh-btn").addEventListener("click", manualRefresh);
  $("#theme-btn").addEventListener("click", () => {
    const dark = document.documentElement.dataset.theme
      ? document.documentElement.dataset.theme === "dark"
      : matchMedia("(prefers-color-scheme: dark)").matches;
    const next = dark ? "light" : "dark";
    setTheme(next);
    store.set("hblive.theme", next);
  });
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden && (!state.lastFetch || Date.now() - state.lastFetch > 20_000)) load();
  });

  renderFilter();
  load();
  setInterval(tick, 1000);
  setInterval(renderStatus, 30_000);
})();
