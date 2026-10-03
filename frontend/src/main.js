import { api, setCsrf, RadioSocket } from "./api.js";
import { renderLogin } from "./pages/login.js";
import { renderRadio } from "./pages/radio.js";

const root = document.getElementById("app");

// Every screen at least 700 px wide (tablets AND big screens) gets the phone-style tabs (Radio / Filters / Levels / Audio) with the
// compact larger layout. "?device=desktop" (remembered) switches a browser back to the old all-in-one page; "?device=auto" resets it.
function setDevice() {
  let forced = "";
  try {
    const q = new URLSearchParams(location.search).get("device");
    if (q === "tablet" || q === "desktop") localStorage.setItem("rr.device", q);
    else if (q === "auto") localStorage.removeItem("rr.device");
    forced = localStorage.getItem("rr.device") || "";
  } catch { /* storage unavailable */ }
  const tablet = forced ? forced === "tablet" : true;
  document.documentElement.toggleAttribute("data-tablet", tablet && window.innerWidth >= 700);
}
setDevice();
addEventListener("resize", setDevice);
let socket = null;

async function boot() {
  socket?.close(); socket = null;
  const st = await api("/api/status");
  window.__loadedBuild ??= st.build;            // remember which release this page was loaded from
  if (st.setup_required) return renderLogin(root, true, boot);
  try {
    const me = await api("/api/me");
    setCsrf(me.csrf);
    socket = renderRadio(root, { onLogout: async () => { await api("/api/logout", "POST"); boot(); }, onAuthLost: boot });
  } catch {
    renderLogin(root, false, boot);
  }
}
boot();

// Installable PWA: app-shell cache only (never API/WebSocket). Needs HTTPS or localhost; ignored otherwise.
if ("serviceWorker" in navigator && window.isSecureContext) {
  navigator.serviceWorker.register("/sw.js").catch(() => { /* optional */ });
}
