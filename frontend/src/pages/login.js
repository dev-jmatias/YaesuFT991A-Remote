import { api, setCsrf } from "../api.js";

export function renderLogin(root, setup, done) {
  root.innerHTML = "";
  const f = document.createElement("form");
  f.className = "card login";
  f.innerHTML = `<h1>Radio Remote</h1>
    <div class="dim">${setup ? "First run: create the administrator account." : "Sign in to continue."}</div>
    <input name="username" placeholder="Username" autocomplete="username" required>
    <input name="password" type="password" placeholder="Password${setup ? " (min 10 characters)" : ""}" autocomplete="${setup ? "new-password" : "current-password"}" required>
    <button type="submit" class="active">${setup ? "Create account" : "Sign in"}</button>
    <div class="err" role="alert"></div>`;
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
