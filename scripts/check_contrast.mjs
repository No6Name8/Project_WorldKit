// WCAG contrast check of every text/background token pair in viewer/assets/tokens.css, dark and light themes.
//   node scripts/check_contrast.mjs          exits 1 if any text pair is below 4.5:1 (or a non-text pair below 3:1)
// Tinted backgrounds (rgba) are composited over the surface they sit on before measuring.
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const css = readFileSync(join(dirname(fileURLToPath(import.meta.url)), "..", "viewer", "assets", "tokens.css"), "utf8");
const block = (sel) => {
  const i = css.indexOf(sel + " {");
  if (i < 0) throw new Error("no block " + sel);
  const body = css.slice(i, css.indexOf("\n}", i));
  return Object.fromEntries([...body.matchAll(/(--[\w-]+):\s*([^;]+);/g)].map((m) => [m[1], m[2].trim()]));
};
const dark = block(":root");
const themes = { dark, light: { ...dark, ...block(':root[data-theme="light"]') } };

const resolve = (t, v, depth = 0) => {
  const m = /^var\((--[\w-]+)\)$/.exec(v);
  if (!m) return v;
  if (depth > 10 || !(m[1] in t)) throw new Error("unresolved " + v);
  return resolve(t, t[m[1]], depth + 1);
};
const parse = (c) => {
  let m = /^#([0-9a-f]{6})$/i.exec(c);
  if (m) return [parseInt(m[1].slice(0, 2), 16), parseInt(m[1].slice(2, 4), 16), parseInt(m[1].slice(4, 6), 16), 1];
  m = /^rgba?\(([\d.]+),\s*([\d.]+),\s*([\d.]+)(?:,\s*([\d.]+))?\)$/.exec(c);
  if (m) return [+m[1], +m[2], +m[3], m[4] === undefined ? 1 : +m[4]];
  throw new Error("not a colour: " + c);
};
const over = (fg, bg) => fg[3] >= 1 ? fg : [0, 1, 2].map((i) => fg[i] * fg[3] + bg[i] * (1 - fg[3])).concat(1);
const lum = (c) => { const f = (v) => { v /= 255; return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4; }; return 0.2126 * f(c[0]) + 0.7152 * f(c[1]) + 0.0722 * f(c[2]); };
const ratio = (a, b) => { const [x, y] = [lum(a), lum(b)].sort((p, q) => q - p); return (x + 0.05) / (y + 0.05); };

// [foreground, background, on (the surface a tinted background sits on), what it is, minimum]
const TEXT = 4.5, UI = 3;
const pairs = [];
for (const bg of ["--bg-app", "--bg-surface", "--bg-raised", "--bg-field", "--vp-background"]) {
  pairs.push(["--text-primary", bg, null, "body text", TEXT], ["--text-secondary", bg, null, "muted text", TEXT]);
}
for (const bg of ["--bg-app", "--bg-surface", "--bg-raised"]) pairs.push(["--accent-text", bg, null, "link / active text", TEXT]);
pairs.push(["--accent-text", "--bg-selected", "--bg-surface", "active chip on selected tint", TEXT]);
pairs.push(["--text-primary", "--bg-selected", "--bg-surface", "text on selected row", TEXT]);
pairs.push(["--text-on-accent", "--accent-solid", null, "primary button", TEXT], ["--text-on-accent", "--accent-solid-hover", null, "primary button hover", TEXT]);
for (const [fg, tint] of [["--status-ok", "--status-ok-bg"], ["--status-warn", "--status-warn-bg"], ["--status-err", "--status-err-bg"], ["--status-idle", "--status-idle-bg"], ["--status-proc", "--status-proc-bg"], ["--found-text", "--teal-tint"]]) {
  for (const on of ["--bg-surface", "--bg-app"]) pairs.push([fg, tint, on, "status chip", TEXT]);
  pairs.push([fg, "--bg-surface", null, "status text on a card", TEXT]);
}
pairs.push(["--text-primary", "--status-err-bg", "--bg-surface", "refused edit row", TEXT], ["--text-secondary", "--status-err-bg", "--bg-surface", "refused edit row, muted", TEXT]);
pairs.push(["--text-primary", "--status-warn-bg", "--bg-surface", "scale banner", TEXT]);
pairs.push(["--text-disabled", "--bg-disabled", null, "disabled control (exempt, informational)", TEXT]);
for (const bg of ["--bg-app", "--bg-surface"]) pairs.push(["--focus-ring", bg, null, "focus ring (non-text)", UI], ["--accent", bg, null, "tab underline, outline (non-text)", UI]);
pairs.push(["--sam", "--vp-background", null, "SAM overlay vs viewport (non-text)", UI]);

let bad = 0;
for (const [name, t] of Object.entries(themes)) {
  console.log(`\n${name} theme`);
  for (const [fg, bg, on, what, min] of pairs) {
    const base = on ? parse(resolve(t, t[on])) : null;
    const b = base ? over(parse(resolve(t, t[bg])), base) : over(parse(resolve(t, t[bg])), parse(resolve(t, t["--bg-surface"])));
    const f = over(parse(resolve(t, t[fg])), b);
    const r = ratio(f, b);
    const ok = r >= min;
    if (!ok) bad++;
    console.log(`${ok ? "PASS" : "FAIL"} ${r.toFixed(2).padStart(5)}:1 (min ${min}) ${fg} on ${bg}${on ? " over " + on : ""}  ${what}`);
  }
}
console.log(`\n${bad ? bad + " pair(s) below the minimum" : "all pairs pass"} (${pairs.length * 2} checked)`);
process.exit(bad ? 1 : 0);
