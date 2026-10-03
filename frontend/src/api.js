// REST + WebSocket client. All control goes through intents; no CAT strings in the browser.
let csrf = "";
export const setCsrf = (t) => { csrf = t; };

export async function api(path, method = "GET", body) {
  const r = await fetch(path, {
    method,
    credentials: "same-origin",
    headers: { "Content-Type": "application/json", "X-CSRF-Token": csrf },
    body: body ? JSON.stringify(body) : undefined,
  });
  const text = await r.text();
  let data = null;
  try { data = text ? JSON.parse(text) : null; } catch { /* plain-text error */ }
  if (!r.ok) throw new Error(data?.error || text || r.statusText);
  return data;
}

export class RadioSocket {
  constructor(handlers) {
    this.h = handlers; this.n = 0; this.pending = new Map(); this.closed = false; this.retry = 1000;
    this.connect();
  }
  connect() {
    const proto = location.protocol === "https:" ? "wss" : "ws";
    const ws = (this.ws = new WebSocket(`${proto}://${location.host}/ws`));
    ws.onopen = () => { this.retry = 1000; this.h.onStatus?.(true); };
    ws.onmessage = (e) => {
      const m = JSON.parse(e.data);
      if (m.t === "ack") {
        const p = this.pending.get(m.id); this.pending.delete(m.id);
        if (p) m.ok ? p.resolve(m.result) : p.reject(new Error(m.error));
      } else this.h.onMessage?.(m);
    };
    ws.onclose = (e) => {
      this.h.onStatus?.(false);
      for (const p of this.pending.values()) p.reject(new Error("disconnected"));
      this.pending.clear();
      if (e.code === 4401) return this.h.onAuthLost?.();
      if (e.code === 4403) { this.closed = true; return this.h.onKicked?.(e.reason); }
      if (!this.closed) setTimeout(() => this.connect(), (this.retry = Math.min(this.retry * 1.6, 8000)));
    };
  }
  send(type, args = {}) {
    if (this.ws.readyState !== 1) return Promise.reject(new Error("not connected"));
    const id = ++this.n;
    return new Promise((resolve, reject) => {
      this.pending.set(id, { resolve, reject });
      this.ws.send(JSON.stringify({ id, type, ...args }));
    });
  }
  // fire-and-forget (heartbeats)
  ping(type) { if (this.ws.readyState === 1) this.ws.send(JSON.stringify({ type })); }
  close() { this.closed = true; this.ws.close(); }
}
