import { api, setCsrf } from "../api.js";

export function renderLogin(root, setup, done) {
  root.innerHTML = "";
  const f = document.createElement("form");
  f.className = "card login";
  f.innerHTML = `<h1>Radio Remote</h1>
    <div class="dim">${setup ? "First run: create the administrator account. Use your callsign as the user name." : "Sign in to continue."}</div>
    <input name="username" placeholder="${setup ? "Callsign (your user name)" : "Callsign or user name"}" autocomplete="username" autocapitalize="characters" spellcheck="false" required>
    <input name="password" type="password" placeholder="Password${setup ? " (min 10 characters)" : ""}" autocomplete="${setup ? "new-password" : "current-password"}" required>
    <label class="chk showpw"><input type="checkbox" name="showpw"> Show password</label>
    <button type="submit" class="active">${setup ? "Create account" : "Sign in"}</button>
    <div class="err" role="alert"></div>`;
  f.showpw.onchange = () => { f.password.type = f.showpw.checked ? "text" : "password"; f.password.focus(); };
  f.onsubmit = async (e) => {
    e.preventDefault();
    const err = f.querySelector(".err"); err.textContent = "";
    try {
      const r = await api(setup ? "/api/setup" : "/api/login", "POST",
        { username: f.username.value, password: f.password.value });
      setCsrf(r.csrf);
      done();
    } catch (ex) { err.textContent = ex.message; }
  };
  root.append(f);
}
