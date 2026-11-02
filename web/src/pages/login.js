import { supabase } from "../supabase.js";
import { esc } from "../lib.js";

export async function renderLogin(root, ctx) {
  if (ctx.user) { ctx.navigate("#/projects"); return; }
  let signUp = false;
  const draw = (msg = "", kind = "") => {
    root.innerHTML = `
      <div class="page-h"><h1>${signUp ? "Create an account" : "Sign in"}</h1></div>
      <form class="stack" id="auth-form" novalidate>
        <label class="field">Email<input id="email" type="email" autocomplete="email" inputmode="email" required /></label>
        <label class="field">Password<input id="password" type="password" autocomplete="${signUp ? "new-password" : "current-password"}" minlength="6" required /></label>
        ${msg ? `<div class="msg ${kind}" role="alert">${esc(msg)}</div>` : ""}
        <div class="pickrow">
          <button id="submit" class="primary big" type="submit">${signUp ? "Create account" : "Sign in"}</button>
          <button id="toggle" class="big" type="button">${signUp ? "I have an account" : "Create an account"}</button>
        </div>
        <p class="hint">${signUp ? "Use at least 6 characters. You may get an email to confirm the address." : "Your projects are private to your account."}</p>
      </form>`;
    const email = root.querySelector("#email"), pw = root.querySelector("#password");
    root.querySelector("#toggle").onclick = () => { signUp = !signUp; draw(); };
    root.querySelector("#auth-form").onsubmit = async (e) => {
      e.preventDefault();
      if (!/^\S+@\S+\.\S+$/.test(email.value)) return draw("Enter a valid email address.", "err");
      if (pw.value.length < 6) return draw("The password needs at least 6 characters.", "err");
      const btn = root.querySelector("#submit");
      btn.disabled = true; btn.textContent = "Please wait";
      const creds = { email: email.value.trim(), password: pw.value };
      const { data, error } = signUp ? await supabase.auth.signUp(creds) : await supabase.auth.signInWithPassword(creds);
      if (error) return draw(error.message, "err");
      if (signUp && !data.session) { signUp = false; return draw("Account created. Check your email and confirm the address, then sign in.", "ok"); }
      ctx.navigate("#/projects");
    };
  };
  draw();
}
