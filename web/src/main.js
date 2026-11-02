import "./style.css";
import { supabase, configured } from "./supabase.js";
import { parseRoute, esc } from "./lib.js";
import { renderHome } from "./pages/home.js";
import { renderLogin } from "./pages/login.js";
import { renderNew } from "./pages/new.js";
import { renderProjects } from "./pages/projects.js";
import { renderProject } from "./pages/project.js";

const app = document.getElementById("app");
const ctx = { session: null, user: null, navigate: (h) => { location.hash = h; }, rerender: () => route() };
let cleanup = null;

const LOGO = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linejoin="round" stroke-linecap="square" aria-hidden="true"><path d="M12 2.5 20.5 7v10L12 21.5 3.5 17V7z"/><path d="M3.5 7 12 11.5 20.5 7M12 11.5v10"/></svg>';

function shell(inner, { bare = false } = {}) {
  const signedIn = Boolean(ctx.user);
  app.innerHTML = `
    ${bare ? "" : `<header id="topbar">
      <a id="brand" href="#/" aria-label="WorldKit home">${LOGO}<b>WorldKit</b><span>Film it. Print it.</span></a>
      <div class="spacer"></div>
      <nav aria-label="Main">
        ${signedIn ? `<a class="btn" href="#/projects">Projects</a><a class="btn primary" href="#/new">New</a><span id="who">${esc(ctx.user.email)}</span><button id="logout" type="button">Log out</button>`
                   : `<a class="btn primary" href="#/login">Sign in</a>`}
      </nav>
    </header>`}
    <main id="page">${inner}</main>`;
  const lo = document.getElementById("logout");
  if (lo) lo.onclick = async () => { await supabase.auth.signOut(); ctx.navigate("#/"); };
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
  const page = shell("");
  if (!configured && r.name !== "home") { page.innerHTML = notConfigured(); return; }
  const map = { home: renderHome, login: renderLogin, new: renderNew, projects: renderProjects };
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
