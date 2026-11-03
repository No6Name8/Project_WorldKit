import "./style.css";
import { supabase, configured } from "./supabase.js";
import { parseRoute, esc } from "./lib.js";
import { renderHome } from "./pages/home.js";
import { renderLogin } from "./pages/login.js";
import { renderNew } from "./pages/new.js";
import { renderProjects } from "./pages/projects.js";
import { renderProject } from "./pages/project.js";
import { renderHelp } from "./pages/help.js";

const app = document.getElementById("app");
const ctx = { session: null, user: null, navigate: (h) => { location.hash = h; }, rerender: () => route() };
let cleanup = null;

const LOGO = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linejoin="round" stroke-linecap="round" aria-hidden="true"><path d="M12 2.5 20.5 7v10L12 21.5 3.5 17V7z"/><path d="M3.5 7 12 11.5 20.5 7"/><path d="M12 11.5v10" style="stroke:var(--fg-accent)"/></svg>';

const CRUMBS = { home: [["Projects", "#/projects"], ["New project", "#/new"]], projects: [["Projects"]], new: [["Projects", "#/projects"], ["New project"]], help: [["Help"]], login: [["Sign in"]] };
const crumbHtml = (name) => (CRUMBS[name] || []).map(([t, h], i, a) => `${i ? '<span class="sep" aria-hidden="true">/</span>' : ""}${h ? `<a href="${h}">${t}</a>` : `<b>${t}</b>`}`).join("");

function shell(inner, { bare = false, route: routeName = "" } = {}) {
  const signedIn = Boolean(ctx.user);
  const initial = signedIn ? esc((ctx.user.email || "?")[0].toUpperCase()) : "";
  app.innerHTML = `
    ${bare ? "" : `<header id="topbar">
      <a id="brand" href="#/" aria-label="WorldKit home">${LOGO}<b>WorldKit</b></a>
      <nav id="crumb" aria-label="Breadcrumb">${crumbHtml(routeName)}</nav>
      <nav id="topnav" aria-label="Main">
        ${signedIn ? `<a href="#/projects">Projects</a><span class="sep" aria-hidden="true">/</span>
          <div class="menu"><button id="user-btn" type="button" aria-haspopup="menu" aria-expanded="false" aria-label="Account menu"><span class="avatar" aria-hidden="true">${initial}</span><span id="who">${esc(ctx.user.email)}</span></button>
            <div id="user-pop" role="menu" hidden><div class="hint" id="user-mail">${esc(ctx.user.email)}</div><a role="menuitem" href="#/help">Help</a><button id="logout" type="button" role="menuitem">Log out</button></div></div>`
                   : `<a href="#/help">Help</a><span class="sep" aria-hidden="true">/</span><a href="#/login">Sign in</a>`}
      </nav>
    </header>`}
    <main id="page" class="page-${routeName}">${inner}</main>
    ${bare ? "" : `<footer id="statusbar"><span>WorldKit</span><span class="mono">STL <i aria-hidden="true">/</i> mm</span></footer>`}`;
  const lo = document.getElementById("logout");
  if (lo) lo.onclick = async () => { await supabase.auth.signOut(); ctx.navigate("#/"); };
  const ub = document.getElementById("user-btn"), pop = document.getElementById("user-pop");
  if (ub) {
    const close = () => { pop.hidden = true; ub.setAttribute("aria-expanded", "false"); };
    ub.onclick = (e) => { e.stopPropagation(); const open = pop.hidden; pop.hidden = !open; ub.setAttribute("aria-expanded", String(open)); };
    document.addEventListener("click", (e) => { if (!pop.contains(e.target)) close(); }, { once: false });
    pop.addEventListener("click", close);
    document.addEventListener("keydown", (e) => { if (e.key === "Escape") close(); });
  }
  return document.getElementById("page");
}

function notConfigured() {
  return `<div class="msg warn" role="alert"><b>This site is not connected to Supabase yet.</b>
    <p class="hint" style="margin-top:var(--space-1)">Set VITE_SUPABASE_URL and VITE_SUPABASE_PUBLISHABLE_KEY (see web/.env.example), then rebuild. The demo viewer works without them.</p></div>`;
}

async function route() {
  if (cleanup) { try { cleanup(); } catch (e) { /* ignore */ } cleanup = null; }
  const r = parseRoute(location.hash);
  const needsAuth = ["new", "projects", "project"].includes(r.name);
  if (needsAuth && configured && !ctx.user) { ctx.navigate("#/login"); return; }
  if (r.name === "project") {  // the viewer takes the whole screen
    app.innerHTML = "";
    cleanup = await renderProject(app, r.id, ctx);
    return;
  }
  const page = shell("", { route: r.name });
  if (!configured && r.name !== "home") { page.innerHTML = notConfigured(); return; }
  const map = { home: renderHome, login: renderLogin, new: renderNew, projects: renderProjects, help: renderHelp };
  cleanup = await map[r.name](page, ctx);
}

async function boot() {
  app.innerHTML = '<div class="boot">Loading</div>';
  if (configured) {
    const { data } = await supabase.auth.getSession();
    ctx.session = data.session; ctx.user = data.session ? data.session.user : null;
    supabase.auth.onAuthStateChange((_e, session) => {
      const was = ctx.user && ctx.user.id;
      ctx.session = session; ctx.user = session ? session.user : null;
      if ((ctx.user && ctx.user.id) !== was) route();
    });
  }
  window.addEventListener("hashchange", route);
  await route();
}

boot();

if ("serviceWorker" in navigator && import.meta.env.PROD) {
  window.addEventListener("load", () => navigator.serviceWorker.register("/sw.js").catch(() => { /* the app works without it */ }));
}
