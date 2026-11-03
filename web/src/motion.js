// Motion helpers for the web app. Durations and the curve come from the tokens (tokens.css); everything here moves only
// transform and opacity, runs once, and is skipped under prefers-reduced-motion. Content is visible without JavaScript:
// the hidden "before reveal" state is only applied by this code.

export const reducedMotion = () => window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;

/** Fade and rise elements in as they enter the viewport, 40 ms apart among siblings, once. */
export function reveal(els) {
  const list = [...els];
  if (!list.length || reducedMotion() || !("IntersectionObserver" in window)) return;
  const io = new IntersectionObserver((entries) => {
    let n = 0;
    for (const e of entries) {
      if (!e.isIntersecting) continue;
      e.target.style.setProperty("--i", String(n++));
      e.target.classList.add("in");
      io.unobserve(e.target);
    }
  }, { rootMargin: "0px 0px -40px 0px" });
  for (const el of list) { el.classList.add("rv"); io.observe(el); }
}

/** Theme: "light" or "dark" on <html data-theme>, remembered in localStorage; the viewer iframe follows through the storage event. */
export function currentTheme() { return document.documentElement.dataset.theme === "light" ? "light" : "dark"; }
export function setTheme(t) {
  document.documentElement.dataset.theme = t;
  try { localStorage.setItem("wk-theme", t); } catch (e) { /* storage blocked: lasts for this page */ }
  const meta = document.querySelector('meta[name="theme-color"]');
  if (meta) meta.content = getComputedStyle(document.documentElement).getPropertyValue("--theme-color").trim();
  for (const f of document.querySelectorAll("iframe")) { try { f.contentWindow.__setTheme && f.contentWindow.__setTheme(t); } catch (e) { /* cross-origin */ } }
}

/** The sticky top bar gets its border and shadow only after the page scrolls. */
export function watchScroll() {
  const on = () => { const bar = document.getElementById("topbar"); if (bar) bar.classList.toggle("scrolled", window.scrollY > 2); };
  window.addEventListener("scroll", on, { passive: true });
  return on;
}

/** The primary button shimmers once, on the first page load of the session. */
export function shimmerOnce(root) {
  let seen = false;
  try { seen = sessionStorage.getItem("wk-shimmer") === "1"; sessionStorage.setItem("wk-shimmer", "1"); } catch (e) { seen = true; }
  if (seen || reducedMotion()) return;
  const b = root.querySelector(".primary");
  if (b) b.classList.add("shimmer-once");
}
